# ai/context_helpers.py - Hàm trợ context dùng chung (tránh circular import)
import json
from typing import Any, Dict, List, Optional, Tuple

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


# Ngưỡng mặc định để lọc trùng khi build context (pgvector): >= threshold thì coi là cùng nội dung, chỉ giữ một
CONTEXT_DEDUPE_SIMILARITY_THRESHOLD = 0.90


def filter_context_items_by_embedding(
    items: List[Dict[str, Any]],
    similarity_threshold: float = CONTEXT_DEDUPE_SIMILARITY_THRESHOLD,
    embedding_key: str = "embedding",
) -> List[Dict[str, Any]]:
    """
    Lọc các mục trùng/near-duplicate theo embedding trước khi đưa vào context.
    Giữ thứ tự; với mỗi mục có embedding, chỉ giữ lại nếu không quá giống (>= threshold) với mục đã giữ trước đó.
    Mục không có embedding luôn được giữ. Tránh nhồi cùng nội dung nhiều lần vào context.
    """
    if not items or similarity_threshold <= 0:
        return items
    kept: List[Dict[str, Any]] = []
    for item in items:
        emb = item.get(embedding_key)
        if emb is None:
            kept.append(item)
            continue
        if isinstance(emb, str):
            try:
                emb = json.loads(emb)
            except Exception:
                kept.append(item)
                continue
        if not isinstance(emb, (list, tuple)) or len(emb) == 0:
            kept.append(item)
            continue
        emb_list = list(emb)
        if not all(isinstance(x, (int, float)) for x in emb_list):
            kept.append(item)
            continue
        too_similar = False
        for k in kept:
            ke = k.get(embedding_key)
            if ke is None:
                continue
            if isinstance(ke, str):
                try:
                    ke = json.loads(ke)
                except Exception:
                    continue
            if not isinstance(ke, (list, tuple)) or len(ke) != len(emb_list):
                continue
            ke_list = list(ke)
            if not all(isinstance(x, (int, float)) for x in ke_list):
                continue
            if _cosine_sim(emb_list, ke_list) >= similarity_threshold:
                too_similar = True
                break
        if not too_similar:
            kept.append(item)
    return kept


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


def _get_rules_by_type(
    project_id: str,
    arc_id: Optional[str] = None,
    types: Optional[List[str]] = None,
) -> List[str]:
    """
    Lấy danh sách nội dung rule theo phạm vi (global/project/arc) và type.
    - types = None: lấy mọi type.
    - types = ["Style", "Method", "Info", "Unknown"] (hoặc subset): chỉ lấy các type đó.
    Chỉ lấy rule đã approve (approve = TRUE).
    """
    try:
        services = init_services()
        if not services:
            return []
        supabase = services["supabase"]
        lines: List[str] = []

        def _add_rows(q):
            try:
                r = q.execute()
            except Exception:
                return
            for row in (r.data or []):
                c = (row.get("content") or "").strip()
                if not c:
                    continue
                lines.append(f"- {c}")

        base_types = None
        if types:
            # Chuẩn hóa type về dạng đầu chữ hoa (Style/Method/Info/Unknown)
            base_types = [str(t).strip().capitalize() for t in types if str(t).strip()]

        # Global rules
        try:
            qg = (
                supabase.table("project_rules")
                .select("content, type")
                .eq("scope", "global")
                .eq("approve", True)
            )
            if base_types:
                qg = qg.in_("type", base_types)
            _add_rows(qg)
        except Exception:
            pass

        if project_id:
            # Project-level rules
            try:
                qp = (
                    supabase.table("project_rules")
                    .select("content, type")
                    .eq("scope", "project")
                    .eq("story_id", project_id)
                    .eq("approve", True)
                )
                if base_types:
                    qp = qp.in_("type", base_types)
                _add_rows(qp)
            except Exception:
                pass

            # Arc-level rules: hỗ trợ 1 rule gắn nhiều arc qua project_rule_arcs (V9.3+)
            if arc_id:
                try:
                    # Lấy toàn bộ rule scope='arc' cho project, lọc theo arc_id và type ở Python
                    qa_rules = (
                        supabase.table("project_rules")
                        .select("id, content, type, arc_id")
                        .eq("scope", "arc")
                        .eq("story_id", project_id)
                        .eq("approve", True)
                    ).execute()
                    arc_rules = list(qa_rules.data or [])
                    if arc_rules:
                        # Map rule_id -> {arc_ids...} từ project_rule_arcs
                        rule_ids = [row.get("id") for row in arc_rules if row.get("id")]
                        arc_map = {}
                        if rule_ids:
                            try:
                                m = (
                                    supabase.table("project_rule_arcs")
                                    .select("rule_id, arc_id")
                                    .in_("rule_id", rule_ids)
                                    .execute()
                                )
                                for r in (m.data or []):
                                    rid = r.get("rule_id")
                                    aid = r.get("arc_id")
                                    if not rid or not aid:
                                        continue
                                    arc_map.setdefault(str(rid), set()).add(str(aid))
                            except Exception:
                                arc_map = {}

                        target_arc_id = str(arc_id)
                        for row in arc_rules:
                            rid = row.get("id")
                            if not rid:
                                continue
                            row_arc_id = row.get("arc_id")
                            applies = False
                            if row_arc_id and str(row_arc_id) == target_arc_id:
                                applies = True
                            elif target_arc_id in arc_map.get(str(rid), set()):
                                applies = True
                            if not applies:
                                continue
                            if base_types and (row.get("type") or "").strip().capitalize() not in base_types:
                                continue
                            c = (row.get("content") or "").strip()
                            if not c:
                                continue
                            lines.append(f"- {c}")
                except Exception:
                    pass

        return lines
    except Exception as e:
        print(f"_get_rules_by_type error: {e}")
        return []


def get_mandatory_rules(project_id: str, arc_id: Optional[str] = None) -> str:
    """
    Lấy luật theo phạm vi: global (không giới hạn project), project, arc.
    Nguồn: project_rules (approve = TRUE), mọi type (Style/Method/Info/Unknown).
    Legacy story_bible [RULE] không còn được dùng.
    """
    lines = _get_rules_by_type(project_id, arc_id, types=None)
    if lines:
        return "\n🔥 --- MANDATORY RULES ---\n" + "\n".join(lines) + "\n"
    return ""


def get_rules_for_intent_prompt(project_id: str, arc_id: Optional[str] = None) -> str:
    """
    Lấy rule cho bước 1 (intent_only_classifier):
    - Chỉ lấy rule type Info (approve = TRUE) để LLM chọn relevant_rules từ nhóm Info.
    """
    lines = _get_rules_by_type(project_id, arc_id, types=["Info"])
    return "\n".join(lines) if lines else ""


def get_rules_by_type_block(project_id: str, arc_id: Optional[str], types: List[str]) -> str:
    """
    Helper: trả về block text cho một hoặc nhiều type (dùng khi build context cuối).
    """
    lines = _get_rules_by_type(project_id, arc_id, types=types)
    return "\n".join(lines) if lines else ""


def _parse_embedding_vector(raw: Any) -> Optional[List[float]]:
    """Chuẩn hóa embedding lấy từ DB (list/tuple hoặc chuỗi '[0.1,0.2,...]') về list[float]."""
    if raw is None:
        return None
    try:
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except Exception:
                # Thử bỏ ngoặc vuông rồi tách tay nếu không phải JSON hợp lệ
                txt = raw.strip()
                if txt.startswith("[") and txt.endswith("]"):
                    txt = txt[1:-1]
                parts = [p for p in txt.split(",") if p.strip()]
                raw = [float(p) for p in parts]
        if not isinstance(raw, (list, tuple)) or not raw:
            return None
        vec: List[float] = []
        for x in raw:
            try:
                vec.append(float(x))
            except (TypeError, ValueError):
                return None
        return vec
    except Exception:
        return None


def get_relevant_info_rules(
    project_id: str,
    user_prompt: str,
    arc_id: Optional[str] = None,
    threshold: float = 0.75,
    candidate_rules_block: Optional[str] = None,
) -> str:
    """
    Lấy các Info Rule (type='Info', approve=TRUE) có embedding giống câu hỏi hiện tại.
    - Dùng embedding câu hỏi so với embedding rule (cosine similarity).
    - Chỉ chọn các rule có similarity >= threshold (mặc định 0.75).
    - Ưu tiên scope: global + project (+ arc nếu có).
    """
    if not project_id or not user_prompt or threshold <= 0:
        return ""
    try:
        from ai.service import AIService  # tránh circular với ai_engine

        q_vec_raw = AIService.get_embedding(user_prompt)
        q_vec = _parse_embedding_vector(q_vec_raw)
        if not q_vec:
            return ""

        services = init_services()
        if not services:
            return ""
        supabase = services["supabase"]

        # Nếu có block candidate (từ relevant_rules / included_rules_text), chỉ giới hạn Info rules trong nhóm đó.
        candidate_contents: Optional[set] = None
        if candidate_rules_block:
            try:
                candidate_contents = set()
                for line in (candidate_rules_block.splitlines() or []):
                    s = (line or "").strip()
                    if not s:
                        continue
                    if s.startswith("-"):
                        s = s[1:].strip()
                    if s:
                        candidate_contents.add(s)
                if not candidate_contents:
                    candidate_contents = None
            except Exception:
                candidate_contents = None

        def _collect(q) -> List[Dict[str, Any]]:
            try:
                r = q.execute()
            except Exception:
                return []
            return list(r.data or [])

        rows: List[Dict[str, Any]] = []

        # Global Info rules
        try:
            qg = (
                supabase.table("project_rules")
                .select("content, embedding, scope, type")
                .eq("scope", "global")
                .eq("type", "Info")
                .eq("approve", True)
                .not_.is_("embedding", "NULL")
            )
            if candidate_contents:
                qg = qg.in_("content", list(candidate_contents))
            rows.extend(_collect(qg))
        except Exception:
            pass

        # Project Info rules
        try:
            qp = (
                supabase.table("project_rules")
                .select("content, embedding, scope, type")
                .eq("scope", "project")
                .eq("story_id", project_id)
                .eq("type", "Info")
                .eq("approve", True)
                .not_.is_("embedding", "NULL")
            )
            if candidate_contents:
                qp = qp.in_("content", list(candidate_contents))
            rows.extend(_collect(qp))
        except Exception:
            pass

        # Arc Info rules (hỗ trợ multi-arc qua project_rule_arcs)
        if arc_id:
            try:
                qa_rules = (
                    supabase.table("project_rules")
                    .select("id, content, embedding, scope, type, arc_id")
                    .eq("scope", "arc")
                    .eq("story_id", project_id)
                    .eq("type", "Info")
                    .eq("approve", True)
                    .not_.is_("embedding", "NULL")
                ).execute()
                arc_rules = list(qa_rules.data or [])
                if arc_rules:
                    rule_ids = [row.get("id") for row in arc_rules if row.get("id")]
                    arc_map = {}
                    if rule_ids:
                        try:
                            m = (
                                supabase.table("project_rule_arcs")
                                .select("rule_id, arc_id")
                                .in_("rule_id", rule_ids)
                                .execute()
                            )
                            for r in (m.data or []):
                                rid = r.get("rule_id")
                                aid = r.get("arc_id")
                                if not rid or not aid:
                                    continue
                                arc_map.setdefault(str(rid), set()).add(str(aid))
                        except Exception:
                            arc_map = {}

                    target_arc_id = str(arc_id)
                    for row in arc_rules:
                        rid = row.get("id")
                        if not rid:
                            continue
                        row_arc_id = row.get("arc_id")
                        applies = False
                        if row_arc_id and str(row_arc_id) == target_arc_id:
                            applies = True
                        elif target_arc_id in arc_map.get(str(rid), set()):
                            applies = True
                        if not applies:
                            continue
                        if candidate_contents and (row.get("content") or "").strip() not in candidate_contents:
                            continue
                        rows.append(row)
            except Exception:
                pass

        if not rows:
            return ""

        seen_contents = set()
        lines: List[str] = []
        for row in rows:
            emb = _parse_embedding_vector(row.get("embedding"))
            if not emb or len(emb) != len(q_vec):
                continue
            sim = _cosine_sim(q_vec, emb)
            if sim >= threshold:
                c = (row.get("content") or "").strip()
                if not c or c in seen_contents:
                    continue
                seen_contents.add(c)
                lines.append(f"- {c}")

        return "\n".join(lines) if lines else ""
    except Exception as e:
        print(f"get_relevant_info_rules error: {e}")
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
        rows_ordered = [row for _, row in scored]
        rows_deduped = filter_context_items_by_embedding(rows_ordered, similarity_threshold=CONTEXT_DEDUPE_SIMILARITY_THRESHOLD)
        lines = []
        for row in rows_deduped[:top_k]:
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
        rows_ordered = [row for _, row in scored]
        rows_deduped = filter_context_items_by_embedding(rows_ordered, similarity_threshold=CONTEXT_DEDUPE_SIMILARITY_THRESHOLD)
        lines = []
        for row in rows_deduped[:top_k]:
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
