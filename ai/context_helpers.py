# ai/context_helpers.py - Hàm trợ context dùng chung (tránh circular import)
import json
from typing import Any, List, Optional, Tuple

from config import init_services


def _cosine_sim(a: List[float], b: List[float]) -> float:
    """Cosine similarity giữa hai vector. Trả về 0 nếu invalid."""
    if not a or not b or len(a) != len(b):
        return 0.0
    try:
        dot = sum(x * y for x, y in zip(a, b))
        na = sum(x * x for x in a) ** 0.5
        nb = sum(y * y for y in b) ** 0.5
        if na <= 0 or nb <= 0:
            return 0.0
        return dot / (na * nb)
    except Exception:
        return 0.0


def get_archived_bible_ids(project_id: str) -> set:
    """V7.7: Lấy set id các story_bible đã archived (không đưa vào context)."""
    try:
        services = init_services()
        if not services:
            return set()
        r = (
            services["supabase"]
            .table("story_bible")
            .select("id")
            .eq("story_id", project_id)
            .eq("archived", True)
            .execute()
        )
        return {row["id"] for row in (r.data or []) if row.get("id")}
    except Exception:
        return set()


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
    """Lấy tất cả các luật (RULE) bắt buộc từ story_bible (bỏ qua entry đã archived)."""
    try:
        services = init_services()
        if not services:
            return ""
        supabase = services["supabase"]
        q = supabase.table("story_bible").select("description").eq(
            "story_id", project_id
        ).ilike("entity_name", "%[RULE]%")
        try:
            q = q.or_("archived.is.null,archived.eq.false")
        except Exception:
            pass
        res = q.execute()
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


def get_top_relations_by_query(project_id: str, query_text: str, top_k: int = 5) -> str:
    """Khi entity_relations đã có embedding: lấy quan hệ gần nhất với query (vector). Trả về chuỗi để đưa vào context; rỗng nếu không có embedding hoặc lỗi."""
    if not query_text or not query_text.strip() or not project_id:
        return ""
    try:
        from ai.service import AIService
        qvec = AIService.get_embedding(query_text.strip()[:4000])
        if not qvec:
            return ""
        services = init_services()
        if not services:
            return ""
        supabase = services["supabase"]
        r = supabase.table("entity_relations").select(
            "id, source_entity_id, target_entity_id, relation_type, description, embedding"
        ).eq("story_id", project_id).not_.is_("embedding", "null").limit(80).execute()
        rows = list(r.data or [])
        if not rows:
            return ""
        id_to_name = {}
        for row in rows:
            for key in ("source_entity_id", "target_entity_id"):
                eid = row.get(key)
                if eid and eid not in id_to_name:
                    id_to_name[eid] = None
        if id_to_name:
            sb = supabase.table("story_bible").select("id, entity_name").eq("story_id", project_id).in_("id", list(id_to_name.keys())).execute()
            if sb.data:
                for x in sb.data:
                    id_to_name[x.get("id")] = (x.get("entity_name") or "").strip()
        scored = []
        for row in rows:
            emb = row.get("embedding")
            if isinstance(emb, str):
                try:
                    emb = json.loads(emb)
                except Exception:
                    emb = None
            if isinstance(emb, list) and len(emb) == len(qvec):
                sim = _cosine_sim(emb, qvec)
                scored.append((sim, row))
        scored.sort(key=lambda x: -x[0])
        lines = []
        for _, row in scored[:top_k]:
            src = id_to_name.get(row.get("source_entity_id"), "")
            tgt = id_to_name.get(row.get("target_entity_id"), "")
            rtype = (row.get("relation_type") or "").strip()
            if src or tgt:
                lines.append(f"  • {src or '?'} — {rtype} — {tgt or '?'}")
        if not lines:
            return ""
        return "Quan hệ liên quan (theo vector):\n" + "\n".join(lines)
    except Exception as e:
        print(f"get_top_relations_by_query error: {e}")
        return ""


def get_top_timeline_by_query(project_id: str, query_text: str, top_k: int = 5) -> str:
    """Khi timeline_events đã có embedding: lấy sự kiện gần nhất với query (vector). Trả về chuỗi để đưa vào context; rỗng nếu không có embedding hoặc lỗi."""
    if not query_text or not query_text.strip() or not project_id:
        return ""
    try:
        from ai.service import AIService
        qvec = AIService.get_embedding(query_text.strip()[:4000])
        if not qvec:
            return ""
        services = init_services()
        if not services:
            return ""
        supabase = services["supabase"]
        r = supabase.table("timeline_events").select(
            "id, title, description, raw_date, event_type, embedding"
        ).eq("story_id", project_id).not_.is_("embedding", "null").limit(80).execute()
        rows = list(r.data or [])
        if not rows:
            return ""
        scored = []
        for row in rows:
            emb = row.get("embedding")
            if isinstance(emb, str):
                try:
                    emb = json.loads(emb)
                except Exception:
                    emb = None
            if isinstance(emb, list) and len(emb) == len(qvec):
                sim = _cosine_sim(emb, qvec)
                scored.append((sim, row))
        scored.sort(key=lambda x: -x[0])
        lines = []
        for _, row in scored[:top_k]:
            title = (row.get("title") or "").strip()
            desc = (row.get("description") or "").strip()[:150]
            raw = (row.get("raw_date") or "").strip()
            if title:
                lines.append(f"  • {title}" + (f" — {desc}" if desc else "") + (f" ({raw})" if raw else ""))
        if not lines:
            return ""
        return "Sự kiện timeline liên quan (theo vector):\n" + "\n".join(lines)
    except Exception as e:
        print(f"get_top_timeline_by_query error: {e}")
        return ""
