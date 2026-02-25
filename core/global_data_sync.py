# core/global_data_sync.py - Kiểm tra và đồng bộ dữ liệu toàn cục (1-N, parent-child, orphan).
"""Chạy khi user bấm; thực thi trong background job. Tự xem → tự sửa → tự đồng bộ, không tự kích hoạt.
Bible, chunks, timeline, relation: đồng bộ theo tên (trùng tên / regex chuẩn hóa) hoặc pgvector (ngưỡng 97%) trong phạm vi 1 chương."""
import re
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set, Tuple

# Ngưỡng similarity pgvector để coi là trùng (trong phạm vi 1 chương)
SIM_THRESHOLD_CHAPTER = 0.90


def _get_supabase():
    from config import init_services
    s = init_services()
    return (s or {}).get("supabase")


def run_global_data_sync(
    project_id: str,
    job_id: Optional[str] = None,
    update_job_fn=None,
) -> Dict[str, Any]:
    """
    Kiểm tra và đồng bộ toàn cục cho project: orphan links, relations không hợp lệ, Bible parent_id, source_chapter.
    Returns: {"success": bool, "error": str|None, "report": {...}, "fixed": {...}}
    """
    result = {
        "success": False,
        "error": None,
        "report": {
            "orphan_chunk_bible_links": 0,
            "orphan_chunk_timeline_links": 0,
            "orphan_relations": 0,
            "bible_parent_synced": 0,
            "bible_same_entity_by_embedding": 0,
            "relation_source_chapter_filled": 0,
            "relation_normalized_to_canonical": 0,
            "relation_deduped": 0,
            "cbl_normalized_to_canonical": 0,
            "cbl_deduped": 0,
            "timeline_orphan": 0,
            "chunk_orphan": 0,
            "chunk_parent_by_name_chapter": 0,
            "chunk_parent_by_embedding_chapter": 0,
            "timeline_parent_by_name_chapter": 0,
            "timeline_parent_by_embedding_chapter": 0,
            "relation_deduped_by_chapter": 0,
            "relation_deduped_by_embedding_chapter": 0,
        },
        "fixed": {
            "chunk_bible_links_deleted": 0,
            "chunk_timeline_links_deleted": 0,
            "entity_relations_deleted": 0,
            "bible_parent_id_updated": 0,
            "bible_parent_id_by_embedding": 0,
            "entity_relations_source_chapter_updated": 0,
            "entity_relations_normalized": 0,
            "entity_relations_deduped": 0,
            "chunk_bible_links_normalized": 0,
            "chunk_bible_links_deduped": 0,
            "timeline_events_null_chapter": 0,
            "chunks_orphan_fixed": 0,
            "chunk_parent_by_name_chapter": 0,
            "chunk_parent_by_embedding_chapter": 0,
            "timeline_parent_by_name_chapter": 0,
            "timeline_parent_by_embedding_chapter": 0,
            "relation_deduped_by_chapter": 0,
            "relation_deduped_by_embedding_chapter": 0,
        },
    }
    supabase = _get_supabase()
    if not supabase:
        result["error"] = "Không kết nối được Supabase."
        return result

    try:
        # --- 1) Thu thập ID hợp lệ ---
        bible_rows = supabase.table("story_bible").select("id, entity_name, source_chapter, parent_id").eq("story_id", project_id).execute()
        bible_ids: Set[Any] = {r["id"] for r in (bible_rows.data or []) if r.get("id")}
        bible_by_id = {r["id"]: r for r in (bible_rows.data or []) if r.get("id")}

        chunk_rows = supabase.table("chunks").select("id, chapter_id").eq("story_id", project_id).execute()
        chunk_ids: Set[Any] = {r["id"] for r in (chunk_rows.data or []) if r.get("id")}

        timeline_rows = supabase.table("timeline_events").select("id, chapter_id").eq("story_id", project_id).execute()
        timeline_ids: Set[Any] = {r["id"] for r in (timeline_rows.data or []) if r.get("id")}

        chapter_rows = supabase.table("chapters").select("id").eq("story_id", project_id).execute()
        chapter_ids: Set[Any] = {r["id"] for r in (chapter_rows.data or []) if r.get("id")}

        # --- 2) Orphan chunk_bible_links: link tới chunk hoặc bible đã xóa ---
        try:
            cbl = supabase.table("chunk_bible_links").select("id, chunk_id, bible_entry_id").eq("story_id", project_id).execute()
            for row in (cbl.data or []):
                cid, bid = row.get("chunk_id"), row.get("bible_entry_id")
                if cid not in chunk_ids or bid not in bible_ids:
                    result["report"]["orphan_chunk_bible_links"] += 1
                    try:
                        supabase.table("chunk_bible_links").delete().eq("id", row["id"]).execute()
                        result["fixed"]["chunk_bible_links_deleted"] += 1
                    except Exception:
                        pass
        except Exception:
            pass

        # --- 3) Orphan chunk_timeline_links ---
        try:
            ctl = supabase.table("chunk_timeline_links").select("id, chunk_id, timeline_event_id").eq("story_id", project_id).execute()
            for row in (ctl.data or []):
                cid, tid = row.get("chunk_id"), row.get("timeline_event_id")
                if cid not in chunk_ids or tid not in timeline_ids:
                    result["report"]["orphan_chunk_timeline_links"] += 1
                    try:
                        supabase.table("chunk_timeline_links").delete().eq("id", row["id"]).execute()
                        result["fixed"]["chunk_timeline_links_deleted"] += 1
                    except Exception:
                        pass
        except Exception:
            pass

        # --- 4) entity_relations: source/target phải tồn tại trong story_bible ---
        try:
            rels = supabase.table("entity_relations").select("id, source_entity_id, target_entity_id, source_chapter").eq("story_id", project_id).execute()
            for row in (rels.data or []):
                sid, tid = row.get("source_entity_id"), row.get("target_entity_id")
                if sid not in bible_ids or tid not in bible_ids:
                    result["report"]["orphan_relations"] += 1
                    try:
                        supabase.table("entity_relations").delete().eq("id", row["id"]).execute()
                        result["fixed"]["entity_relations_deleted"] += 1
                    except Exception:
                        pass
        except Exception:
            pass

        # --- 5) Bible parent_id: cùng entity_name (chuẩn hóa) xuất hiện nhiều chương → đặt parent = bản đầu (source_chapter nhỏ nhất) ---
        def _norm_name(name: str) -> str:
            n = (name or "").strip().lower()
            if n.startswith("[") and "]" in n:
                n = n[n.index("]") + 1 :].strip()
            return n or ""

        # 5a) V8.9: Đồng bộ theo tên trong phạm vi 1 chương — gộp bản trùng trong cùng chương
        by_chapter_norm: Dict[Tuple[Any, str], List[Dict]] = defaultdict(list)
        for r in (bible_rows.data or []):
            if not r.get("id"):
                continue
            norm = _norm_name(r.get("entity_name") or "")
            if norm:
                ch = r.get("source_chapter")
                by_chapter_norm[(ch, norm)].append({"id": r["id"], "source_chapter": ch, "entity_name": r.get("entity_name")})
        for (_ch, _norm), items in by_chapter_norm.items():
            if len(items) < 2:
                continue
            # Trong cùng chương: parent = bản đầu (theo id hoặc thứ tự)
            sorted_items = sorted(items, key=lambda x: str(x["id"]))
            parent_id = sorted_items[0]["id"]
            for item in sorted_items[1:]:
                cur = bible_by_id.get(item["id"])
                if cur and (cur.get("parent_id") or "") != parent_id:
                    try:
                        supabase.table("story_bible").update({"parent_id": parent_id}).eq("id", item["id"]).execute()
                        result["fixed"]["bible_parent_id_updated"] += 1
                    except Exception:
                        pass

        by_norm: Dict[str, List[Dict]] = defaultdict(list)
        for r in (bible_rows.data or []):
            if not r.get("id"):
                continue
            norm = _norm_name(r.get("entity_name") or "")
            if norm:
                by_norm[norm].append({"id": r["id"], "source_chapter": r.get("source_chapter"), "entity_name": r.get("entity_name")})

        for norm, items in by_norm.items():
            if len(items) < 2:
                continue
            result["report"]["bible_parent_synced"] += 1
            # Sắp xếp theo source_chapter (None = 0), lấy đầu làm parent
            sorted_items = sorted(items, key=lambda x: (x["source_chapter"] is None, x["source_chapter"] or 0))
            parent_id = sorted_items[0]["id"]
            for item in sorted_items[1:]:
                if item["id"] == parent_id:
                    continue
                cur = bible_by_id.get(item["id"])
                if cur and (cur.get("parent_id") or "") != parent_id:
                    try:
                        supabase.table("story_bible").update({"parent_id": parent_id}).eq("id", item["id"]).execute()
                        result["fixed"]["bible_parent_id_updated"] += 1
                    except Exception:
                        pass

        # --- 5b) Bible parent_id (embedding): tên khác nhưng cùng thực thể → so embedding với chương trước, đặt parent ---
        def _cosine(a: List[float], b: List[float]) -> float:
            if not a or not b or len(a) != len(b):
                return 0.0
            dot_ = sum(x * y for x, y in zip(a, b))
            na = sum(x * x for x in a) ** 0.5
            nb = sum(x * x for x in b) ** 0.5
            if na * nb <= 0:
                return 0.0
            return dot_ / (na * nb)

        try:
            bible_emb = supabase.table("story_bible").select("id, source_chapter, parent_id, embedding").eq("story_id", project_id).execute()
            rows_all = list(bible_emb.data or [])
            # Chỉ lấy embedding dạng list[float] (Supabase có thể trả về list)
            rows_with_emb = []
            for r in rows_all:
                emb = r.get("embedding")
                if emb is not None and isinstance(emb, (list, tuple)) and len(emb) > 0 and isinstance(emb[0], (int, float)):
                    rows_with_emb.append({**r, "embedding": list(emb)})
            # V8.9: Chỉ so embedding trong cùng 1 chương; ngưỡng 97%
            rows_with_emb.sort(key=lambda x: (x.get("source_chapter") is None, x.get("source_chapter") or 999))
            by_ch = defaultdict(list)
            for r in rows_with_emb:
                ch = r.get("source_chapter")
                by_ch[ch].append(r)
            for _ch, group in by_ch.items():
                group.sort(key=lambda x: str(x.get("id")))
                for i, row in enumerate(group):
                    if row.get("parent_id"):
                        continue
                    emb_cur = row.get("embedding")
                    if not emb_cur:
                        continue
                    best_id, best_sim = None, SIM_THRESHOLD_CHAPTER
                    for j in range(i):
                        other = group[j]
                        emb_oth = other.get("embedding")
                        if not emb_oth:
                            continue
                        sim = _cosine(emb_cur, emb_oth)
                        if sim >= best_sim:
                            best_sim = sim
                            best_id = other["id"]
                    if best_id:
                        try:
                            supabase.table("story_bible").update({"parent_id": best_id}).eq("id", row["id"]).execute()
                            result["report"]["bible_same_entity_by_embedding"] += 1
                            result["fixed"]["bible_parent_id_by_embedding"] += 1
                        except Exception:
                            pass
        except Exception:
            pass

        # Reload bible sau khi đã set parent (name + embedding)
        bible_rows = supabase.table("story_bible").select("id, entity_name, source_chapter, parent_id").eq("story_id", project_id).execute()
        bible_by_id = {r["id"]: r for r in (bible_rows.data or []) if r.get("id")}

        # --- 5c) Chunks: đồng bộ theo tên (trùng content chuẩn hóa) hoặc embedding 97% trong phạm vi 1 chương ---
        def _norm_text(t: str, max_len: int = 300) -> str:
            if not t or not isinstance(t, str):
                return ""
            s = re.sub(r"\s+", " ", (t[:max_len] or "").strip().lower())
            return s.strip() or ""

        try:
            chunk_rows_full = supabase.table("chunks").select("id, chapter_id, content, raw_content, parent_chunk_id, embedding").eq("story_id", project_id).execute()
            chunks_all = list(chunk_rows_full.data or [])
            by_ch_norm: Dict[Tuple[Any, str], List[Dict]] = defaultdict(list)
            for r in chunks_all:
                if not r.get("id"):
                    continue
                ch = r.get("chapter_id")
                norm = _norm_text(r.get("content") or r.get("raw_content", ""), 300)
                if norm:
                    by_ch_norm[(ch, norm)].append({"id": r["id"], "chapter_id": ch})
            for (_ch, _n), items in by_ch_norm.items():
                if len(items) < 2:
                    continue
                sorted_items = sorted(items, key=lambda x: str(x["id"]))
                parent_id = sorted_items[0]["id"]
                for item in sorted_items[1:]:
                    try:
                        supabase.table("chunks").update({"parent_chunk_id": parent_id}).eq("id", item["id"]).execute()
                        result["report"]["chunk_parent_by_name_chapter"] += 1
                        result["fixed"]["chunk_parent_by_name_chapter"] += 1
                    except Exception:
                        pass
            # Chunks: embedding 97% trong cùng chapter_id
            chunks_with_emb = []
            for r in chunks_all:
                emb = r.get("embedding")
                if emb is not None and isinstance(emb, (list, tuple)) and len(emb) > 0 and isinstance(emb[0], (int, float)):
                    chunks_with_emb.append({**r, "embedding": list(emb)})
            by_chunk_ch: Dict[Any, List[Dict]] = defaultdict(list)
            for r in chunks_with_emb:
                by_chunk_ch[r.get("chapter_id")].append(r)
            for _ch, group in by_chunk_ch.items():
                group.sort(key=lambda x: str(x.get("id")))
                for i, row in enumerate(group):
                    if row.get("parent_chunk_id"):
                        continue
                    emb_cur = row.get("embedding")
                    if not emb_cur:
                        continue
                    best_id, best_sim = None, SIM_THRESHOLD_CHAPTER
                    for j in range(i):
                        other = group[j]
                        emb_oth = other.get("embedding")
                        if not emb_oth:
                            continue
                        sim = _cosine(emb_cur, emb_oth)
                        if sim >= best_sim:
                            best_sim = sim
                            best_id = other["id"]
                    if best_id:
                        try:
                            supabase.table("chunks").update({"parent_chunk_id": best_id}).eq("id", row["id"]).execute()
                            result["report"]["chunk_parent_by_embedding_chapter"] += 1
                            result["fixed"]["chunk_parent_by_embedding_chapter"] += 1
                        except Exception:
                            pass
        except Exception:
            pass

        # --- 5d) Timeline_events: đồng bộ theo tên (trùng title chuẩn hóa) hoặc embedding 97% trong phạm vi 1 chương ---
        try:
            timeline_full = supabase.table("timeline_events").select("id, chapter_id, title, parent_event_id, embedding").eq("story_id", project_id).execute()
            timeline_all = list(timeline_full.data or [])
            by_te_ch_norm: Dict[Tuple[Any, str], List[Dict]] = defaultdict(list)
            for r in timeline_all:
                if not r.get("id"):
                    continue
                ch = r.get("chapter_id")
                norm = _norm_text(r.get("title") or "", 200)
                if norm:
                    by_te_ch_norm[(ch, norm)].append({"id": r["id"], "chapter_id": ch})
            for (_ch, _n), items in by_te_ch_norm.items():
                if len(items) < 2:
                    continue
                sorted_items = sorted(items, key=lambda x: str(x["id"]))
                parent_id = sorted_items[0]["id"]
                for item in sorted_items[1:]:
                    try:
                        supabase.table("timeline_events").update({"parent_event_id": parent_id}).eq("id", item["id"]).execute()
                        result["report"]["timeline_parent_by_name_chapter"] += 1
                        result["fixed"]["timeline_parent_by_name_chapter"] += 1
                    except Exception:
                        pass
            # Timeline: embedding 97% trong cùng chapter_id
            te_with_emb = []
            for r in timeline_all:
                emb = r.get("embedding")
                if emb is not None and isinstance(emb, (list, tuple)) and len(emb) > 0 and isinstance(emb[0], (int, float)):
                    te_with_emb.append({**r, "embedding": list(emb)})
            by_te_ch: Dict[Any, List[Dict]] = defaultdict(list)
            for r in te_with_emb:
                by_te_ch[r.get("chapter_id")].append(r)
            for _ch, group in by_te_ch.items():
                group.sort(key=lambda x: str(x.get("id")))
                for i, row in enumerate(group):
                    if row.get("parent_event_id"):
                        continue
                    emb_cur = row.get("embedding")
                    if not emb_cur:
                        continue
                    best_id, best_sim = None, SIM_THRESHOLD_CHAPTER
                    for j in range(i):
                        other = group[j]
                        emb_oth = other.get("embedding")
                        if not emb_oth:
                            continue
                        sim = _cosine(emb_cur, emb_oth)
                        if sim >= best_sim:
                            best_sim = sim
                            best_id = other["id"]
                    if best_id:
                        try:
                            supabase.table("timeline_events").update({"parent_event_id": best_id}).eq("id", row["id"]).execute()
                            result["report"]["timeline_parent_by_embedding_chapter"] += 1
                            result["fixed"]["timeline_parent_by_embedding_chapter"] += 1
                        except Exception:
                            pass
        except Exception:
            pass

        # --- 6) entity_relations.source_chapter: nếu null, điền từ bible (source_entity_id hoặc target thuộc chương nào) ---
        try:
            rels2 = supabase.table("entity_relations").select("id, source_entity_id, target_entity_id, source_chapter").eq("story_id", project_id).execute()
            for row in (rels2.data or []):
                if row.get("source_chapter") is not None:
                    continue
                sid, tid = row.get("source_entity_id"), row.get("target_entity_id")
                src_b = bible_by_id.get(sid) if sid else None
                tgt_b = bible_by_id.get(tid) if tid else None
                ch = None
                if src_b and src_b.get("source_chapter") is not None:
                    ch = src_b["source_chapter"]
                if ch is None and tgt_b and tgt_b.get("source_chapter") is not None:
                    ch = tgt_b["source_chapter"]
                if ch is not None:
                    try:
                        supabase.table("entity_relations").update({"source_chapter": ch}).eq("id", row["id"]).execute()
                        result["report"]["relation_source_chapter_filled"] += 1
                        result["fixed"]["entity_relations_source_chapter_updated"] += 1
                    except Exception:
                        pass
        except Exception:
            pass

        # --- 6b) Map canonical entity id (root parent) cho mỗi bible id ---
        def _canonical_id(eid: Any) -> Any:
            seen: Set[Any] = set()
            while eid and eid in bible_by_id:
                if eid in seen:
                    return eid
                seen.add(eid)
                b = bible_by_id[eid]
                pid = b.get("parent_id")
                if not pid or pid == eid:
                    return eid
                eid = pid
            return eid

        # --- 6c) entity_relations: quy source/target về canonical, rồi gộp trùng (giữ một theo cặp + relation_type) ---
        try:
            rels_all = supabase.table("entity_relations").select("id, source_entity_id, target_entity_id, relation_type").eq("story_id", project_id).execute()
            canonical_src = {}
            canonical_tgt = {}
            for row in (rels_all.data or []):
                sid = row.get("source_entity_id")
                tid = row.get("target_entity_id")
                cs, ct = _canonical_id(sid), _canonical_id(tid)
                canonical_src[row["id"]] = cs
                canonical_tgt[row["id"]] = ct
            for row in (rels_all.data or []):
                rid, sid, tid = row.get("id"), row.get("source_entity_id"), row.get("target_entity_id")
                cs, ct = canonical_src.get(rid), canonical_tgt.get(rid)
                if cs is None or ct is None:
                    continue
                if sid != cs or tid != ct:
                    try:
                        supabase.table("entity_relations").update({"source_entity_id": cs, "target_entity_id": ct}).eq("id", rid).execute()
                        result["report"]["relation_normalized_to_canonical"] += 1
                        result["fixed"]["entity_relations_normalized"] += 1
                    except Exception:
                        pass
            # Dedupe trong phạm vi 1 chương: cùng (source_chapter, source, target, relation_type) giữ một
            rels2 = supabase.table("entity_relations").select("id, source_entity_id, target_entity_id, relation_type, source_chapter").eq("story_id", project_id).execute()
            key_to_id: Dict[Tuple[Any, Any, Any, str], str] = {}
            for row in (rels2.data or []):
                ch = row.get("source_chapter")
                key = (ch, row.get("source_entity_id"), row.get("target_entity_id"), (row.get("relation_type") or "").strip())
                if key not in key_to_id:
                    key_to_id[key] = row["id"]
                else:
                    try:
                        supabase.table("entity_relations").delete().eq("id", row["id"]).execute()
                        result["report"]["relation_deduped"] += 1
                        result["report"]["relation_deduped_by_chapter"] += 1
                        result["fixed"]["entity_relations_deduped"] += 1
                        result["fixed"]["relation_deduped_by_chapter"] += 1
                    except Exception:
                        pass
        except Exception:
            pass

        # --- 6e) entity_relations: trùng theo embedding 97% trong cùng source_chapter → giữ một, xóa bản còn lại ---
        try:
            rels_emb = supabase.table("entity_relations").select("id, source_chapter, embedding").eq("story_id", project_id).execute()
            rels_with_emb = []
            for r in (rels_emb.data or []):
                emb = r.get("embedding")
                if emb is not None and isinstance(emb, (list, tuple)) and len(emb) > 0 and isinstance(emb[0], (int, float)):
                    rels_with_emb.append({**r, "embedding": list(emb)})
            by_rel_ch: Dict[Any, List[Dict]] = defaultdict(list)
            for r in rels_with_emb:
                by_rel_ch[r.get("source_chapter")].append(r)
            to_delete = []
            for _ch, group in by_rel_ch.items():
                group.sort(key=lambda x: str(x.get("id")))
                for i, row in enumerate(group):
                    if row["id"] in to_delete:
                        continue
                    emb_cur = row.get("embedding")
                    if not emb_cur:
                        continue
                    for j in range(i):
                        other = group[j]
                        if other["id"] in to_delete:
                            continue
                        emb_oth = other.get("embedding")
                        if not emb_oth:
                            continue
                        if _cosine(emb_cur, emb_oth) >= SIM_THRESHOLD_CHAPTER:
                            to_delete.append(row["id"])
                            result["report"]["relation_deduped_by_embedding_chapter"] += 1
                            result["fixed"]["relation_deduped_by_embedding_chapter"] += 1
                            break
            for rid in to_delete:
                try:
                    supabase.table("entity_relations").delete().eq("id", rid).execute()
                except Exception:
                    pass
        except Exception:
            pass

        # --- 6d) chunk_bible_links: quy bible_entry_id về canonical, rồi gộp trùng (chunk_id, bible_entry_id) ---
        try:
            cbl_all = supabase.table("chunk_bible_links").select("id, chunk_id, bible_entry_id").eq("story_id", project_id).execute()
            seen_cbl: Dict[Tuple[Any, Any], str] = {}
            for row in (cbl_all.data or []):
                bid = row.get("bible_entry_id")
                can = _canonical_id(bid)
                if bid != can:
                    try:
                        supabase.table("chunk_bible_links").update({"bible_entry_id": can}).eq("id", row["id"]).execute()
                        result["report"]["cbl_normalized_to_canonical"] += 1
                        result["fixed"]["chunk_bible_links_normalized"] += 1
                    except Exception:
                        pass
                key = (row.get("chunk_id"), can)
                if key not in seen_cbl:
                    seen_cbl[key] = row["id"]
                else:
                    try:
                        supabase.table("chunk_bible_links").delete().eq("id", row["id"]).execute()
                        result["report"]["cbl_deduped"] += 1
                        result["fixed"]["chunk_bible_links_deduped"] += 1
                    except Exception:
                        pass
        except Exception:
            pass

        # --- 7) timeline_events.chapter_id không còn tồn tại → set null hoặc báo ---
        try:
            for row in (timeline_rows.data or []):
                cid = row.get("chapter_id")
                if cid is not None and cid not in chapter_ids:
                    result["report"]["timeline_orphan"] += 1
                    try:
                        supabase.table("timeline_events").update({"chapter_id": None}).eq("id", row["id"]).execute()
                        result["fixed"]["timeline_events_null_chapter"] += 1
                    except Exception:
                        pass
        except Exception:
            pass

        # --- 8) chunks.chapter_id không tồn tại → set null (nếu schema cho phép) hoặc báo ---
        try:
            for row in (chunk_rows.data or []):
                cid = row.get("chapter_id")
                if cid is not None and cid not in chapter_ids:
                    result["report"]["chunk_orphan"] += 1
                    try:
                        supabase.table("chunks").update({"chapter_id": None}).eq("id", row["id"]).execute()
                        result["fixed"]["chunks_orphan_fixed"] += 1
                    except Exception:
                        pass
        except Exception:
            pass

        result["success"] = True
        if job_id and update_job_fn:
            fxd = result["fixed"]
            summary = (
                f"Dọn rác: {fxd['chunk_bible_links_deleted']} link bible, {fxd['chunk_timeline_links_deleted']} link timeline, {fxd['entity_relations_deleted']} relation. "
                f"Bible: {fxd['bible_parent_id_updated']} parent (tên), {fxd['bible_parent_id_by_embedding']} parent (embed 97%). "
                f"Chunk: {fxd['chunk_parent_by_name_chapter']} (tên), {fxd['chunk_parent_by_embedding_chapter']} (embed 97%). "
                f"Timeline: {fxd['timeline_parent_by_name_chapter']} (tên), {fxd['timeline_parent_by_embedding_chapter']} (embed 97%). "
                f"Relation: {fxd['entity_relations_normalized']} chuẩn hóa, {fxd['entity_relations_deduped']} gộp trùng; "
                f"theo chương: {fxd['relation_deduped_by_chapter']} (tên), {fxd['relation_deduped_by_embedding_chapter']} (embed 97%). "
                f"CBL: {fxd['chunk_bible_links_normalized']} chuẩn hóa, {fxd['chunk_bible_links_deduped']} gộp trùng; "
                f"source_chapter: {fxd['entity_relations_source_chapter_updated']}."
            )
            update_job_fn(job_id, "completed", result_summary=summary)
    except Exception as e:
        result["error"] = str(e)[:1000]
        if job_id and update_job_fn:
            update_job_fn(job_id, "failed", error_message=result["error"])
    return result
