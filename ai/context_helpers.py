# ai/context_helpers.py - Hàm trợ context dùng chung (tránh circular import)
from typing import Any, List, Optional, Tuple

from config import init_services


def get_related_chapter_nums(project_id: str, target_bible_entities: List[str]) -> List[int]:
    """Lấy danh sách chapter_number có liên quan đến các entity (reverse lookup). Dùng cho fallback read_full_content khi search_context trả lời chưa đủ."""
    if not project_id or not target_bible_entities:
        return []
    try:
        services = init_services()
        if not services:
            return []
        supabase = services["supabase"]
        related = set()
        for entity in target_bible_entities:
            if not (entity or str(entity).strip()):
                continue
            res = supabase.table("story_bible").select("source_chapter").eq(
                "story_id", project_id
            ).ilike("entity_name", f"%{entity}%").execute()
            if res.data:
                for row in res.data:
                    if row.get("source_chapter") and row["source_chapter"] > 0:
                        related.add(int(row["source_chapter"]))
        return sorted(related)
    except Exception as e:
        print(f"get_related_chapter_nums error: {e}")
        return []


def get_mandatory_rules(project_id: str) -> str:
    """Lấy tất cả các luật (RULE) bắt buộc từ story_bible."""
    try:
        services = init_services()
        if not services:
            return ""
        supabase = services["supabase"]
        res = supabase.table("story_bible").select("description").eq(
            "story_id", project_id
        ).ilike("entity_name", "%[RULE]%").execute()
        if res.data:
            rules_text = "\n".join([f"- {r['description']}" for r in res.data])
            return f"\n🔥 --- MANDATORY RULES ---\n{rules_text}\n"
        return ""
    except Exception as e:
        print(f"Error getting rules: {e}")
        return ""


def resolve_chapter_range(
    project_id: str,
    chapter_range_mode: Optional[str],
    chapter_range_count: int,
    chapter_range: Optional[List[int]],
) -> Optional[Tuple[int, int]]:
    """Trả về (start, end) chapter_number từ router. first/latest query DB; range dùng trực tiếp."""
    try:
        services = init_services()
        if not services:
            return None
        supabase = services["supabase"]
        count = max(1, min(50, int(chapter_range_count) if chapter_range_count else 5))

        if chapter_range_mode == "range" and chapter_range and len(chapter_range) >= 2:
            return (int(chapter_range[0]), int(chapter_range[1]))

        if chapter_range_mode == "first":
            r = supabase.table("chapters").select("chapter_number").eq(
                "story_id", project_id
            ).order("chapter_number").limit(1).execute()
            if r.data and len(r.data) > 0:
                start = int(r.data[0].get("chapter_number", 1))
                return (start, start + count - 1)
            return (1, count)

        if chapter_range_mode == "latest":
            r = supabase.table("chapters").select("chapter_number").eq(
                "story_id", project_id
            ).order("chapter_number", desc=True).limit(1).execute()
            if r.data and len(r.data) > 0:
                end = int(r.data[0].get("chapter_number", 1))
                start = max(1, end - count + 1)
                return (start, end)
            return (1, count)

    except Exception as e:
        print(f"_resolve_chapter_range error: {e}")
    return None


def get_entity_relations(entity_id: Any, project_id: str) -> str:
    """Lấy quan hệ của entity: từ bảng entity_relations và parent_id từ story_bible. Trả về chuỗi dạng '> [RELATION]: ...'."""
    lines = []
    try:
        services = init_services()
        if not services:
            return ""
        supabase = services["supabase"]

        try:
            rel_res = supabase.table("entity_relations").select("*").or_(
                f"source_entity_id.eq.{entity_id},target_entity_id.eq.{entity_id}"
            ).execute()
        except Exception:
            try:
                rel_res = supabase.table("entity_relations").select("*").or_(
                    f"entity_id.eq.{entity_id},target_entity_id.eq.{entity_id}"
                ).execute()
            except Exception:
                rel_res = None
        if rel_res and rel_res.data:
            id_to_name = {}
            for r in rel_res.data:
                eid = r.get("entity_id") or r.get("source_entity_id") or r.get("from_entity_id")
                tid = r.get("target_entity_id") or r.get("to_entity_id")
                if eid and eid not in id_to_name:
                    id_to_name[eid] = None
                if tid and tid not in id_to_name:
                    id_to_name[tid] = None
            if id_to_name:
                sb = supabase.table("story_bible").select("id, entity_name").eq(
                    "story_id", project_id
                ).in_("id", list(id_to_name.keys())).execute()
                if sb.data:
                    for row in sb.data:
                        id_to_name[row.get("id")] = row.get("entity_name") or ""
            for r in rel_res.data:
                rel_type = r.get("relation_type") or r.get("relation") or "liên quan"
                eid = r.get("entity_id") or r.get("source_entity_id") or r.get("from_entity_id")
                tid = r.get("target_entity_id") or r.get("to_entity_id")
                name_a = id_to_name.get(eid) if eid else ""
                name_b = id_to_name.get(tid) if tid else ""
                if name_a or name_b:
                    lines.append(f"> [RELATION]: {name_a or 'Entity'} là {rel_type} của {name_b or 'Entity'}.")

        try:
            variants = supabase.table("story_bible").select("entity_name, description").eq(
                "story_id", project_id
            ).eq("parent_id", entity_id).execute()
            if variants.data:
                for v in variants.data:
                    name = v.get("entity_name") or ""
                    desc = (v.get("description") or "")[:200]
                    if name:
                        lines.append(f"> [RELATION]: Biến thể: {name} — {desc}...")
        except Exception:
            pass
    except Exception as e:
        print(f"get_entity_relations error: {e}")
    return "\n".join(lines) if lines else ""
