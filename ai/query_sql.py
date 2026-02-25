# ai/query_sql.py - query_Sql: VALID_QUERY_TARGETS, infer_query_target, build_query_sql_context
import re
from typing import Dict, Optional, Tuple

from config import init_services

from ai.context_helpers import get_entity_relations, get_mandatory_rules, resolve_chapter_range
from ai.hybrid_search import HybridSearch
from ai.utils import (
    _filter_bible_by_chapter_range,
    format_bible_context_by_sections,
    get_timeline_events,
)

VALID_QUERY_TARGETS = ("chapters", "rules", "bible_entity", "chunks", "timeline", "relation", "summary", "art")


def infer_query_target(user_prompt: str, router_result: Dict) -> str:
    """
    Rule-based suy luận query_target khi Router/Planner không trả về. Ưu tiên từ khóa rõ ràng.
    """
    existing = (router_result.get("query_target") or "").strip().lower()
    if existing in VALID_QUERY_TARGETS:
        return existing
    q = (user_prompt or "").strip().lower()
    if re.search(r"luật|quy tắc|rule\s", q):
        return "rules"
    if re.search(r"timeline|sự kiện|mốc thời gian", q):
        return "timeline"
    if re.search(r"quan hệ|relation|với nhau", q):
        return "relation"
    if re.search(r"tóm tắt đã lưu|summary đã|trong hệ thống|crystallize", q):
        return "summary"
    if re.search(r"nghệ thuật|art\s|style", q):
        return "art"
    if re.search(r"chunk|đoạn văn đã tách", q):
        return "chunks"
    if re.search(r"liệt kê chương|danh sách chương|bao nhiêu chương|chương \d+ tên|chương nào", q):
        return "chapters"
    if router_result.get("target_bible_entities") or re.search(r"entity|nhân vật|địa điểm|bible", q):
        return "bible_entity"
    return "bible_entity"


def build_query_sql_context(
    router_result: Dict, project_id: str, arc_id: Optional[str] = None
) -> Tuple[str, str]:
    """
    Xây context cho intent query_Sql theo query_target. Trả về (block_text, source_label).
    Không gọi LLM; dùng truy vấn cố định theo từng loại.
    """
    query_target = (router_result.get("query_target") or "").strip().lower()
    if query_target not in VALID_QUERY_TARGETS:
        query_target = infer_query_target(router_result.get("rewritten_query") or "", router_result)
    chapter_range = router_result.get("chapter_range")
    range_bounds = None
    if project_id and (chapter_range or router_result.get("chapter_range_mode")):
        range_bounds = resolve_chapter_range(
            project_id,
            router_result.get("chapter_range_mode"),
            router_result.get("chapter_range_count", 5),
            chapter_range,
        )
    rewritten = (router_result.get("rewritten_query") or "").strip() or (router_result.get("target_bible_entities") or [""])[0]
    entities = list(router_result.get("target_bible_entities") or [])

    try:
        services = init_services()
        if not services:
            return "", ""
        supabase = services["supabase"]
    except Exception:
        return "", ""

    if query_target == "chapters":
        q = supabase.table("chapters").select("chapter_number, title").eq("story_id", project_id).order("chapter_number")
        if range_bounds and len(range_bounds) >= 2:
            start, end = int(range_bounds[0]), int(range_bounds[1])
            q = q.gte("chapter_number", start).lte("chapter_number", end)
        r = q.execute()
        rows = list(r.data) if r.data else []
        if not rows:
            return "\n--- DANH SÁCH CHƯƠNG (query_Sql) ---\nChưa có chương nào.", "🔍 Query SQL"
        lines = [f"- Chương {row.get('chapter_number')}: {row.get('title') or ''}" for row in rows]
        block = "\n--- DANH SÁCH CHƯƠNG (query_Sql) ---\n" + "\n".join(lines)
        return block, "🔍 Query SQL"

    if query_target == "rules":
        rules_text = get_mandatory_rules(project_id)
        block = "\n--- LUẬT / QUY TẮC (query_Sql) ---\n" + (rules_text.strip() if rules_text else "Chưa có luật nào trong dự án.")
        return block, "🔍 Query SQL"

    if query_target == "bible_entity":
        raw_list = HybridSearch.smart_search_hybrid_raw(rewritten, project_id, top_k=10) if rewritten else []
        if range_bounds and raw_list:
            raw_list = _filter_bible_by_chapter_range(raw_list, range_bounds, max_items=10)
        if raw_list:
            for item in raw_list:
                try:
                    eid = item.get("id")
                    if eid is not None:
                        HybridSearch.update_lookup_stats(eid)
                except Exception:
                    pass
            part = format_bible_context_by_sections(raw_list)
            return "\n--- KNOWLEDGE BASE (query_Sql - bible_entity) ---\n" + part, "🔍 Query SQL"
        return "\n--- KNOWLEDGE BASE (query_Sql - bible_entity) ---\nKhông tìm thấy entity phù hợp.", "🔍 Query SQL"

    if query_target == "chunks":
        chapter_ids = []
        if range_bounds and len(range_bounds) >= 2:
            ch_res = supabase.table("chapters").select("id").eq("story_id", project_id).gte("chapter_number", int(range_bounds[0])).lte("chapter_number", int(range_bounds[1])).execute()
            chapter_ids = [row["id"] for row in (ch_res.data or []) if row.get("id")]
        q = supabase.table("chunks").select("id, chapter_id, content, raw_content, meta_json").eq("story_id", project_id)
        if chapter_ids:
            q = q.in_("chapter_id", chapter_ids)
        r = q.limit(30).execute()
        rows = list(r.data) if r.data else []
        if not rows:
            return "\n--- CHUNKS (query_Sql) ---\nChưa có chunk nào cho khoảng chương đã chọn." if chapter_ids else "\n--- CHUNKS (query_Sql) ---\nChưa có chunk nào.", "🔍 Query SQL"
        lines = []
        for row in rows:
            content = (row.get("content") or row.get("raw_content") or "")[:500]
            if content:
                lines.append(f"[Chương ID {row.get('chapter_id')}] {content}...")
        block = "\n--- CHUNKS (query_Sql) ---\n" + "\n".join(lines[:20])
        return block, "🔍 Query SQL"

    if query_target == "timeline":
        ch_tuple = tuple(chapter_range) if chapter_range and len(chapter_range) >= 2 else None
        events = get_timeline_events(project_id, limit=50, chapter_range=ch_tuple, arc_id=arc_id)
        if not events:
            return "\n--- TIMELINE (query_Sql) ---\nChưa có sự kiện timeline nào." + (" (có thể chưa extract cho khoảng chương này.)" if ch_tuple else ""), "🔍 Query SQL"
        lines = []
        for e in events[:30]:
            title = e.get("title") or ""
            desc = (e.get("description") or "")[:200]
            order = e.get("event_order", "")
            lines.append(f"- [{order}] {title}: {desc}")
        block = "\n--- TIMELINE (query_Sql) ---\n" + "\n".join(lines)
        return block, "🔍 Query SQL"

    if query_target == "relation":
        entity_name = (entities[0] or rewritten or "").strip()
        if not entity_name:
            return "\n--- QUAN HỆ (query_Sql) ---\nChưa chỉ rõ entity (tên nhân vật/địa điểm).", "🔍 Query SQL"
        raw_list = HybridSearch.smart_search_hybrid_raw(entity_name, project_id, top_k=1)
        if not raw_list:
            return "\n--- QUAN HỆ (query_Sql) ---\nKhông tìm thấy entity tương ứng.", "🔍 Query SQL"
        main_id = raw_list[0].get("id")
        rel_text = get_entity_relations(main_id, project_id)
        block = "\n--- QUAN HỆ (query_Sql) ---\n" + (rel_text.strip() if rel_text else "Chưa có quan hệ nào cho entity này.")
        return block, "🔍 Query SQL"

    if query_target == "summary":
        rows = []
        try:
            r_new = supabase.table("chat_crystallize_entries").select("title, description").eq("scope", "project").eq("story_id", project_id).order("created_at", desc=True).limit(10).execute()
            rows = [{"entity_name": r.get("title", ""), "description": r.get("description", "")} for r in (r_new.data or [])]
        except Exception:
            pass
        if not rows:
            try:
                res = supabase.table("story_bible").select("entity_name, description").eq("story_id", project_id).ilike("entity_name", "%[CHAT]%").limit(10).execute()
                rows = list(res.data) if res.data else []
            except Exception:
                pass
        if not rows:
            return "\n--- TÓM TẮT ĐÃ LƯU (query_Sql) ---\nChưa có tóm tắt crystallize nào.", "🔍 Query SQL"
        lines = [f"- {row.get('entity_name', '')}: {(row.get('description') or '')[:400]}..." for row in rows]
        block = "\n--- TÓM TẮT ĐÃ LƯU (query_Sql) ---\n" + "\n".join(lines)
        return block, "🔍 Query SQL"

    if query_target == "art":
        q = supabase.table("chapters").select("chapter_number, title, art_style").eq("story_id", project_id)
        if range_bounds and len(range_bounds) >= 2:
            q = q.gte("chapter_number", int(range_bounds[0])).lte("chapter_number", int(range_bounds[1]))
        r = q.order("chapter_number").limit(20).execute()
        rows = list(r.data) if r.data else []
        if not rows:
            return "\n--- NGHỆ THUẬT / STYLE (query_Sql) ---\nChưa có thông tin art_style cho chương." if range_bounds else "\n--- NGHỆ THUẬT / STYLE (query_Sql) ---\nChưa có dữ liệu.", "🔍 Query SQL"
        lines = []
        for row in rows:
            art = (row.get("art_style") or "").strip()
            if art:
                lines.append(f"- Chương {row.get('chapter_number')} ({row.get('title') or ''}): {art}")
        block = "\n--- NGHỆ THUẬT / STYLE (query_Sql) ---\n" + ("\n".join(lines) if lines else "Chưa có mô tả nghệ thuật.")
        return block, "🔍 Query SQL"

    # fallback: bible_entity
    raw_list = HybridSearch.smart_search_hybrid_raw(rewritten, project_id, top_k=10) if rewritten else []
    if range_bounds and raw_list:
        raw_list = _filter_bible_by_chapter_range(raw_list, range_bounds, max_items=10)
    if raw_list:
        part = format_bible_context_by_sections(raw_list)
        return "\n--- KNOWLEDGE BASE (query_Sql) ---\n" + part, "🔍 Query SQL"
    return "\n--- KNOWLEDGE BASE (query_Sql) ---\nKhông tìm thấy dữ liệu phù hợp.", "🔍 Query SQL"
