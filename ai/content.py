# ai/content.py - suggest_relations, suggest_import_category, generate_*, extract_*, get_file_sample, analyze_split, execute_split
import json
import re
from typing import Any, Dict, List, Optional

from config import Config, init_services

from ai.service import AIService, _get_default_tool_model
from ai.utils import get_bible_entries

try:
    from core.arc_service import ArcService  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    ArcService = None  # type: ignore


def suggest_relations(content: str, story_id: str) -> List[Dict[str, Any]]:
    """
    AI quét nội dung (chương/đoạn) và so khớp với bible_index để đề xuất:
    - Quan hệ giữa hai thực thể: Source, Target, Relation_Type, Reason -> kind="relation".
    - Nhân vật tiến hóa (1-n): thực thể mới cùng gốc -> gợi ý parent_id, kind="parent".
    Dùng vector search để lấy Bible entities liên quan nội dung trước (giảm context LLM, tăng độ chính xác).
    """
    if not content or not content.strip() or not story_id:
        return []
    entries = get_bible_entries(story_id)
    if not entries:
        return []
    # Nếu có nhiều entity: dùng vector search để chỉ đưa entities liên quan nội dung vào LLM (đỡ gọi LLM với danh sách quá dài)
    if len(entries) > 60:
        try:
            from ai.hybrid_search import HybridSearch
            query_snippet = (content[:3000] or "").strip()
            if query_snippet:
                raw = HybridSearch.smart_search_hybrid_raw(query_snippet, story_id, top_k=80)
                if raw:
                    seen_ids = set()
                    entries = []
                    for r in raw:
                        eid = r.get("id")
                        if eid and eid not in seen_ids:
                            seen_ids.add(eid)
                            entries.append(r)
        except Exception:
            pass
    name_to_id = {}
    for e in entries:
        name = (e.get("entity_name") or "").strip()
        if name:
            name_to_id[name] = e.get("id")
    index_text = "\n".join([f"- {e.get('entity_name', '')}" for e in entries[:150]])
    prompt = f"""Bạn là trợ lý phân tích văn bản. Cho NỘI DUNG và DANH SÁCH THỰC THỂ (Bible) của một truyện.

DANH SÁCH THỰC THỂ (chính xác từ Bible):
{index_text}

NỘI DUNG (đoạn/chương cần phân tích):
---
{content[:15000]}
---

Nhiệm vụ (ưu tiên ĐẦY ĐỦ, không bỏ sót):
1) QUAN HỆ: Tìm MỌI cặp thực thể có tương tác/liên quan trong nội dung (trực tiếp hay gián tiếp). Với mỗi cặp: source, target, relation_type, reason. Khi nghi ngờ vẫn liệt kê.
2) NHÂN VẬT TIẾN HÓA (1-n): Nếu có thực thể mới là "phiên bản khác" hoặc liên quan gốc của thực thể đã có -> gợi ý parent: entity, parent, reason.

Trả về ĐÚNG một JSON object với hai key:
- "relations": [ {{ "source": "...", "target": "...", "relation_type": "...", "reason": "..." }} ]
- "parent_suggestions": [ {{ "entity": "...", "parent": "...", "reason": "..." }} ]

Chỉ dùng tên có trong DANH SÁCH THỰC THỂ. Ưu tiên liệt kê nhiều nhất có thể. Nếu không có gì phù hợp, trả về "relations": [] và "parent_suggestions": [].
Chỉ trả về JSON, không giải thích thêm."""

    try:
        response = AIService.call_openrouter(
            messages=[{"role": "user", "content": prompt}],
            model=_get_default_tool_model(),
            temperature=0.15,
            max_tokens=4000,
        )
        text = (response.choices[0].message.content or "").strip()
        text = re.sub(r"^```\w*\n?", "", text).strip()
        text = re.sub(r"\n?```\s*$", "", text).strip()
        data = json.loads(text)
        relations_in = data.get("relations") or []
        parent_in = data.get("parent_suggestions") or []

        def resolve_name(name: str) -> Optional[Any]:
            n = (name or "").strip()
            if n in name_to_id:
                return name_to_id[n]
            for k, vid in name_to_id.items():
                if n in k or k in n:
                    return vid
            return None

        out = []
        for r in relations_in:
            src_id = resolve_name(r.get("source") or "")
            tgt_id = resolve_name(r.get("target") or "")
            if src_id and tgt_id and src_id != tgt_id:
                out.append({
                    "kind": "relation",
                    "source_entity_id": src_id,
                    "target_entity_id": tgt_id,
                    "relation_type": (r.get("relation_type") or "liên quan").strip(),
                    "description": (r.get("reason") or "").strip(),
                    "story_id": story_id,
                })
        for p in parent_in:
            child_id = resolve_name(p.get("entity") or "")
            parent_id = resolve_name(p.get("parent") or "")
            if child_id and parent_id and child_id != parent_id:
                out.append({
                    "kind": "parent",
                    "entity_id": child_id,
                    "parent_entity_id": parent_id,
                    "reason": (p.get("reason") or "").strip(),
                })
        return out
    except Exception as e:
        print(f"suggest_relations error: {e}")
        return []


def suggest_import_category(text: str) -> str:
    """Gợi ý prefix/category cho nội dung import (dùng LLM nhẹ)."""
    if not text or len(text.strip()) < 20:
        return "[OTHER]"
    try:
        model = _get_default_tool_model()
        prefixes = Config.get_prefixes()
        if not prefixes:
            return "[OTHER]"
        if "[OTHER]" not in prefixes:
            prefixes = list(prefixes) + ["[OTHER]"]
        prompt = f"""Phân loại nội dung sau vào ĐÚNG MỘT trong các loại (chỉ trả về chuỗi loại, không giải thích):
{', '.join(prefixes)}

NỘI DUNG (rút gọn):
{text[:1500]}

Trả về đúng một chuỗi, ví dụ: [CHARACTER] hoặc [RULE]."""
        resp = AIService.call_openrouter(
            messages=[{"role": "user", "content": prompt}],
            model=model,
            temperature=0.1,
            max_tokens=50,
        )
        raw = (resp.choices[0].message.content or "").strip()
        for p in prefixes:
            if p in raw or (p.strip("[]") and p.strip("[]").lower() in raw.lower()):
                return p
        return "[OTHER]"
    except Exception as e:
        print(f"suggest_import_category error: {e}")
        return "[OTHER]"


def generate_arc_summary_from_chapters(chapter_summaries: List[Dict[str, Any]], arc_name: str = "") -> Optional[str]:
    """Từ danh sách tóm tắt chương, AI tạo tóm tắt ngắn cho Arc."""
    if not chapter_summaries or not isinstance(chapter_summaries, list):
        return None
    parts = []
    for i, ch in enumerate(chapter_summaries):
        num = ch.get("chapter_number") or ch.get("num") or (i + 1)
        summ = ch.get("summary") or ch.get("description") or ""
        if summ:
            parts.append(f"Chương {num}: {summ}")
    if not parts:
        return None
    combined = "\n".join(parts)
    try:
        model = _get_default_tool_model()
        prompt = f"""Các tóm tắt chương thuộc Arc '{arc_name or "Unnamed"}':

{combined}

Nhiệm vụ: Viết 1 đoạn tóm tắt ngắn gọn (2-5 câu) cho toàn bộ Arc. Chỉ trả về đoạn tóm tắt, không lời dẫn."""
        resp = AIService.call_openrouter(
            messages=[{"role": "user", "content": prompt}],
            model=model,
            temperature=0.3,
            max_tokens=500,
        )
        raw = (resp.choices[0].message.content or "").strip()
        return raw if raw else None
    except Exception as e:
        print(f"generate_arc_summary_from_chapters error: {e}")
        return None


def generate_chapter_metadata(content: str) -> Dict[str, str]:
    """Dùng model để tóm tắt và phân tích art_style. Trả về {"summary": "...", "art_style": "..."}."""
    if not content or not str(content).strip():
        return {"summary": "", "art_style": ""}
    try:
        model = _get_default_tool_model()
        prompt = f"""Phân tích đoạn văn/chương sau và trả về ĐÚNG MỘT JSON với 2 key:
- "summary": Tóm tắt nội dung (2-4 câu, tiếng Việt).
- "art_style": Phong cách viết (1-2 câu).

NỘI DUNG:
{content[:12000]}

Chỉ trả về JSON."""
        response = AIService.call_openrouter(
            messages=[{"role": "user", "content": prompt}],
            model=model,
            temperature=0.2,
            max_tokens=500,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content
        raw = AIService.clean_json_text(raw)
        data = json.loads(raw)
        return {
            "summary": str(data.get("summary", ""))[:2000],
            "art_style": str(data.get("art_style", ""))[:500],
        }
    except Exception as e:
        print(f"generate_chapter_metadata error: {e}")
        return {"summary": "", "art_style": ""}


def _get_supabase():
    try:
        services = init_services()
        return services.get("supabase") if services else None
    except Exception:
        return None


def _get_relevant_bible_for_chunk(
    story_id: str,
    chapter_number: Optional[int],
    arc_id: Optional[str],
) -> List[Dict[str, Any]]:
    """
    Lấy entity Bible liên quan tới một chunk:
    - Base: entity có source_chapter == chapter_number.
    - Expand: entity có quan hệ với các entity base trong scope arc (dựa trên entity_relations + ArcService.get_chapter_scope).
    Fallback: nếu không có chapter_number hoặc lỗi DB → trả về toàn bộ Bible entries (giống hành vi cũ).
    """
    supabase = _get_supabase()
    if not supabase or not story_id:
        return []

    # Nếu không xác định được chương, fallback về hành vi cũ: toàn bộ Bible
    if chapter_number is None:
        try:
            return get_bible_entries(story_id)
        except Exception:
            return []

    # 1) Base Bible: entity được tạo từ chương hiện tại
    try:
        base_res = (
            supabase.table("story_bible")
            .select("id, entity_name, source_chapter")
            .eq("story_id", story_id)
            .eq("source_chapter", chapter_number)
            .execute()
        )
        base_bible: List[Dict[str, Any]] = list(base_res.data or [])
    except Exception:
        base_bible = []

    base_ids = {row.get("id") for row in base_bible if row.get("id") is not None}
    if not base_ids:
        # Không có entity base → fallback: không expand, chỉ dùng base (có thể rỗng)
        return base_bible

    # 2) Scope theo arc: tập chapter_numbers thuộc arc hiện tại (+ các arc trước nếu sequential)
    chapter_numbers_in_scope = set()
    try:
        chapter_numbers_in_scope.add(int(chapter_number))
    except Exception:
        pass

    if ArcService and arc_id:
        try:
            scope = ArcService.get_chapter_scope(story_id, arc_id)  # type: ignore[attr-defined]
            for n in scope.get("chapter_numbers") or []:
                try:
                    chapter_numbers_in_scope.add(int(n))
                except Exception:
                    continue
        except Exception:
            pass

    # 3) Lấy các entity_relations trong scope nói trên và mở rộng entity liên quan
    expanded_ids = set()
    if chapter_numbers_in_scope:
        try:
            rel_res = (
                supabase.table("entity_relations")
                .select("source_entity_id, target_entity_id, source_chapter")
                .eq("story_id", story_id)
                .in_("source_chapter", list(chapter_numbers_in_scope))
                .execute()
            )
            rels = list(rel_res.data or [])
        except Exception:
            rels = []

        for rel in rels:
            src_id = rel.get("source_entity_id")
            tgt_id = rel.get("target_entity_id")
            if src_id in base_ids and tgt_id is not None:
                expanded_ids.add(tgt_id)
            if tgt_id in base_ids and src_id is not None:
                expanded_ids.add(src_id)

    # 4) Query thêm Bible cho các entity mở rộng
    expanded_bible: List[Dict[str, Any]] = []
    if expanded_ids:
        try:
            exp_res = (
                supabase.table("story_bible")
                .select("id, entity_name, source_chapter")
                .eq("story_id", story_id)
                .in_("id", list(expanded_ids))
                .execute()
            )
            expanded_bible = list(exp_res.data or [])
        except Exception:
            expanded_bible = []

    # 5) Hợp base + expanded, tránh trùng id
    merged: Dict[Any, Dict[str, Any]] = {}
    for row in base_bible + expanded_bible:
        rid = row.get("id")
        if rid is not None:
            merged[rid] = row
    return list(merged.values())


def generate_chunk_summary(
    content: str,
    story_id: str,
    chapter_id: Optional[str] = None,
    chapter_number: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Tóm tắt một chunk + gắn entity Bible:
    - summary: 2-4 câu, ưu tiên nêu rõ hành động/trận chiến nếu có.
    - entities: danh sách tên entity trong Bible (giữ nguyên prefix) thực sự xuất hiện/quan trọng trong chunk.
    - embedding: vector embedding cho text tóm tắt (summary + entities).
    """
    text = (content or "").strip()
    if not text or not story_id:
        return {"summary": "", "entities": [], "embedding": None}

    # Chuẩn hóa chapter_number + arc_id (từ chapter_id nếu cần)
    effective_chapter_number: Optional[int] = chapter_number
    arc_id: Optional[str] = None
    if (chapter_id or chapter_number) and story_id:
        supabase = _get_supabase()
        if supabase:
            try:
                if chapter_id:
                    r = (
                        supabase.table("chapters")
                        .select("chapter_number, arc_id")
                        .eq("id", chapter_id)
                        .limit(1)
                        .execute()
                    )
                else:
                    r = (
                        supabase.table("chapters")
                        .select("chapter_number, arc_id")
                        .eq("story_id", story_id)
                        .eq("chapter_number", chapter_number)
                        .limit(1)
                        .execute()
                    )
                row = (r.data or [None])[0] if r and r.data else None
                if row:
                    if effective_chapter_number is None and row.get("chapter_number") is not None:
                        effective_chapter_number = int(row.get("chapter_number"))
                    if row.get("arc_id") is not None:
                        arc_id = row.get("arc_id")
            except Exception:
                pass

    # Lấy danh sách entity Bible liên quan tới chương + arc scope
    try:
        entries = _get_relevant_bible_for_chunk(
            story_id=story_id,
            chapter_number=effective_chapter_number,
            arc_id=arc_id,
        )
    except Exception:
        entries = []

    entity_names: List[str] = []
    if entries:
        for e in entries:
            name = (e.get("entity_name") or "").strip()
            if name:
                entity_names.append(name)
    index_text = "\n".join(f"- {n}" for n in entity_names[:150]) if entity_names else "(Trống)"

    try:
        model = _get_default_tool_model()
        prompt = f"""Bạn là trợ lý tóm tắt CHUNK của một chương truyện.

DANH SÁCH THỰC THỂ (Bible) CỦA TRUYỆN (giữ nguyên prefix loại, ví dụ [CHARACTER], [LOCATION], [EVENT]):
{index_text}

NỘI DUNG CHUNK:
---
{text[:12000]}
---

NHIỆM VỤ (rất quan trọng):
1) Tóm tắt ngắn gọn nội dung chunk bằng tiếng Việt (2-4 câu) — ưu tiên làm rõ:
   - Nếu có TRẬN CHIẾN / HÀNH ĐỘNG: nêu rõ ai đánh với ai, ở đâu, kết quả sơ bộ ra sao.
   - Nếu là đoạn thiết lập bối cảnh, nội tâm, đối thoại: nêu rõ nhân vật chính, mục tiêu đoạn này.
2) Từ DANH SÁCH THỰC THỂ ở trên, chọn ra CÁC ENTITY thực sự xuất hiện hoặc đóng vai trò quan trọng trong chunk.
   - Giữ NGUYÊN tên (kể cả prefix) như trong danh sách, ví dụ: "[CHARACTER] Cường", "[EVENT] Trận chiến ở X".
   - Chỉ chọn những entity có thể suy luận hợp lý là đang xuất hiện trong đoạn văn (qua tên, biệt danh, mô tả).

Trả về ĐÚNG MỘT JSON với 2 key:
- "summary": chuỗi tóm tắt ngắn gọn.
- "entities": mảng tên entity (giữ nguyên như trong danh sách Bible), có thể rỗng nếu không có.

Chỉ trả về JSON, không giải thích thêm."""

        response = AIService.call_openrouter(
            messages=[{"role": "user", "content": prompt}],
            model=model,
            temperature=0.2,
            max_tokens=800,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content
        raw = AIService.clean_json_text(raw)
        data = json.loads(raw)
        summary = str(data.get("summary", "")).strip()
        entities = data.get("entities") or []
        if not isinstance(entities, list):
            entities = []
        entities_out: List[str] = []
        for e in entities:
            s = (e if isinstance(e, str) else str(e)).strip()
            if s:
                entities_out.append(s)

        # Hợp text để embed: entity list + summary
        embed_text_parts: List[str] = []
        if entities_out:
            embed_text_parts.append("Entities: " + ", ".join(entities_out[:20]))
        if summary:
            embed_text_parts.append("Summary: " + summary)
        embed_text = "\n".join(embed_text_parts) if embed_text_parts else text[:4000]

        embedding = None
        try:
            if embed_text:
                embedding = AIService.get_embedding(embed_text)
        except Exception:
            embedding = None

        return {
            "summary": summary[:2000],
            "entities": entities_out[:50],
            "embedding": embedding,
        }
    except Exception as e:
        print(f"generate_chunk_summary error: {e}")
        return {"summary": "", "entities": [], "embedding": None}


def extract_timeline_events_from_content(content: str, chapter_label: str = "") -> List[Dict[str, Any]]:
    """AI trích xuất sự kiện timeline từ nội dung chương."""
    if not content or not str(content).strip():
        return []
    try:
        model = _get_default_tool_model()
        ctx = f"Chương: {chapter_label}\n\n" if chapter_label else ""
        prompt = f"""Trích xuất MỌI SỰ KIỆN theo thứ tự thời gian từ nội dung dưới đây (kể cả thoáng qua, flashback, mốc nhỏ). Mỗi sự kiện: event_order (1,2,...), title, description, raw_date, event_type (event|flashback|milestone|timeskip|other). Ưu tiên đầy đủ, khi nghi ngờ vẫn liệt kê.

{ctx}NỘI DUNG:
{content[:35000]}

Trả về ĐÚNG MỘT JSON với key "events" là mảng. Nếu không có sự kiện, trả về {{ "events": [] }}. Chỉ trả về JSON."""
        response = AIService.call_openrouter(
            messages=[{"role": "user", "content": prompt}],
            model=model,
            temperature=0.15,
            max_tokens=6000,
            response_format={"type": "json_object"},
        )
        raw = (response.choices[0].message.content or "").strip()
        raw = AIService.clean_json_text(raw)
        data = json.loads(raw)
        events = data.get("events") if isinstance(data, dict) else []
        if not isinstance(events, list):
            return []
        out = []
        for i, e in enumerate(events):
            if not isinstance(e, dict):
                continue
            order = int(e.get("event_order", i + 1))
            title = str(e.get("title", "")).strip() or f"Sự kiện {order}"
            desc = str(e.get("description", ""))[:2000]
            raw_date = str(e.get("raw_date", ""))[:200]
            etype = str(e.get("event_type", "event")).lower()
            if etype not in ("event", "flashback", "milestone", "timeskip", "other"):
                etype = "event"
            out.append({
                "event_order": order,
                "title": title,
                "description": desc,
                "raw_date": raw_date,
                "event_type": etype,
            })
        return out
    except Exception as ex:
        print(f"extract_timeline_events_from_content error: {ex}")
        return []


def get_file_sample(file_content: str, sample_size: int = 80) -> str:
    """Lấy mẫu: 80 dòng đầu + 80 giữa + 80 cuối."""
    if not file_content or not str(file_content).strip():
        return ""
    lines = str(file_content).strip().splitlines()
    total_lines = len(lines)
    if total_lines <= sample_size * 3:
        return "\n".join(lines)
    parts = []
    parts.append(f"[ĐẦU FILE - {sample_size} dòng đầu]")
    parts.append("\n".join(lines[:sample_size]))
    mid_start = total_lines // 2 - sample_size // 2
    parts.append(f"\n\n[GIỮA FILE - {sample_size} dòng giữa]")
    parts.append("\n".join(lines[mid_start:mid_start + sample_size]))
    parts.append(f"\n\n[CUỐI FILE - {sample_size} dòng cuối]")
    parts.append("\n".join(lines[-sample_size:]))
    return "\n".join(parts)


def analyze_split_strategy(
    file_content: str,
    file_type: str = "story",
    context_hint: str = "",
) -> Dict[str, Any]:
    """AI phân tích mẫu file để tìm quy luật phân cách. Trả về {"split_type": "...", "split_value": "..."}."""
    if not file_content or not str(file_content).strip():
        return {"split_type": "by_length", "split_value": "2000"}
    sample = get_file_sample(file_content, sample_size=80)
    try:
        model = _get_default_tool_model()
        type_hints = {
            "story": "Truyện - tìm quy luật phân cách chương.",
            "character_data": "Dữ liệu nhân vật - tìm quy luật phân cách entity.",
            "excel_export": "Excel/CSV - cắt theo Sheet hoặc Row count.",
        }
        hint_text = type_hints.get(file_type.strip().lower(), type_hints["story"])
        if context_hint:
            hint_text += f"\nGợi ý: {context_hint}"
        prompt = f"""Phân tích mẫu file và TÌM QUY LUẬT PHÂN CÁCH.

Loại file: {hint_text}

MẪU FILE:
---
{sample}
---

Trả về ĐÚNG MỘT JSON:
- "split_type": "by_keyword" | "by_length" | "by_sheet"
- "split_value": regex/keyword hoặc số ký tự

Ví dụ: {{"split_type": "by_keyword", "split_value": "^Chương\\\\s+\\\\d+"}}
Chỉ trả về JSON."""
        response = AIService.call_openrouter(
            messages=[{"role": "user", "content": prompt}],
            model=model,
            temperature=0.2,
            max_tokens=500,
            response_format={"type": "json_object"},
        )
        raw = (response.choices[0].message.content or "").strip()
        raw = AIService.clean_json_text(raw)
        data = json.loads(raw)
        split_type = data.get("split_type", "by_length")
        split_value = str(data.get("split_value", "2000")).strip()
        if split_type not in ["by_keyword", "by_length", "by_sheet"]:
            split_type = "by_length"
        return {"split_type": split_type, "split_value": split_value}
    except Exception as e:
        print(f"analyze_split_strategy error: {e}")
        return {"split_type": "by_length", "split_value": "2000"}


def _build_smart_regex_pattern(keyword: str) -> str:
    """Xây regex hỗ trợ có dấu/không dấu."""
    keyword_upper = keyword.upper().strip()
    if keyword_upper in ["CHƯƠNG", "CHUONG", "CHAPTER"]:
        return r"(?i)(CHƯƠNG|CHUONG|CHAPTER)\s+\d+[:\s-]*"
    elif keyword_upper in ["PHẦN", "PHAN", "PART"]:
        return r"(?i)(PHẦN|PHAN|PART)\s+\d+[:\s-]*"
    elif keyword_upper in ["---", "***", "==="]:
        return rf"(?i)\s*{re.escape(keyword)}\s*"
    else:
        return rf"(?i)^\s*{re.escape(keyword)}\s*"


def execute_split_logic(
    file_content: str,
    split_type: str,
    split_value: str,
    debug: bool = False,
) -> List[Dict[str, Any]]:
    """Cắt file bằng Python. Trả về list of {"title": str, "content": str, "order": int}."""
    if not file_content or not str(file_content).strip():
        return []
    content = str(file_content).strip()
    out = []
    try:
        if split_type == "by_keyword":
            pattern_str = split_value.strip() or "---"
            is_regex = any(c in pattern_str for c in ["^", "$", "\\d", "\\s", "\\w", "\\+", "\\*", "\\?", "\\[", "\\(", "\\{"])
            if not is_regex:
                pattern_str = _build_smart_regex_pattern(pattern_str)
            try:
                pattern = re.compile(pattern_str, re.IGNORECASE | re.MULTILINE)
            except Exception:
                pattern_str = rf"^\s*{re.escape(split_value.strip())}\s*"
                pattern = re.compile(pattern_str, re.IGNORECASE | re.MULTILINE)
            matches = list(pattern.finditer(content))
            if len(matches) == 0:
                return []
            if matches[0].start() > 0:
                part_content = content[0:matches[0].start()].strip()
                if part_content:
                    out.append({"title": "Phần mở đầu", "content": part_content, "order": 1})
            for i, match in enumerate(matches):
                start = match.end()
                end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
                part_content = content[start:end].strip()
                if not part_content:
                    continue
                title = match.group(0).strip()[:50] if match.group(0) else f"Phần {len(out)+1}"
                if not title or len(title.strip()) < 2:
                    first_line = part_content.splitlines()[0] if part_content.splitlines() else ""
                    title = first_line[:50] if first_line else f"Phần {len(out)+1}"
                out.append({"title": title, "content": part_content, "order": len(out) + 1})
        elif split_type == "by_length":
            chunk_size = int(split_value) if split_value.isdigit() else 2000
            chunk_size = max(500, min(chunk_size, 50000))
            lines = content.splitlines()
            current_chunk = []
            current_len = 0
            chunk_num = 1
            for line in lines:
                line_len = len(line) + 1
                if current_len + line_len > chunk_size and current_chunk:
                    chunk_text = "\n".join(current_chunk).strip()
                    if chunk_text:
                        out.append({"title": f"Phần {chunk_num}", "content": chunk_text, "order": chunk_num})
                        chunk_num += 1
                    current_chunk = [line]
                    current_len = line_len
                else:
                    current_chunk.append(line)
                    current_len += line_len
            if current_chunk:
                chunk_text = "\n".join(current_chunk).strip()
                if chunk_text:
                    out.append({"title": f"Phần {chunk_num}", "content": chunk_text, "order": chunk_num})
        elif split_type == "by_sheet":
            if split_value.lower() == "row count" or split_value.isdigit():
                row_count = int(split_value) if split_value.isdigit() else 100
                lines = content.splitlines()
                for i in range(0, len(lines), row_count):
                    chunk_lines = lines[i:i + row_count]
                    if chunk_lines:
                        out.append({"title": f"Sheet {i // row_count + 1}", "content": "\n".join(chunk_lines), "order": i // row_count + 1})
            else:
                out.append({"title": "Phần 1", "content": content, "order": 1})
        else:
            out.append({"title": "Phần 1", "content": content, "order": 1})
        return out
    except Exception as e:
        print(f"execute_split_logic error: {e}")
        return [{"title": "Phần 1", "content": content, "order": 1}]
