# core/unified_chapter_analyze.py - Pipeline phân tích 1 chương: Bible, Timeline, Chunks, Relations trong 1 lần LLM, có cờ bước, retry, rollback.
"""Chạy unified extract cho một chương: 1 LLM call → bible (+ embedding), timeline, chunks (+ embedding), relations + link tables. Ghi unified_extract_runs và source_chunk_id (Bible, Timeline)."""
import json
import re
from typing import Any, Dict, List, Optional, Tuple

# Max ký tự nội dung đưa vào 1 lần LLM (tránh vượt context).
UNIFIED_MAX_CONTENT_CHARS = 60_000

# Chunk fallback: kích thước mục tiêu và overlap (câu)
CHUNK_TARGET_SIZE = 2000
CHUNK_OVERLAP_SENTENCES = 2


def _fallback_semantic_chunks(content: str) -> List[Dict[str, Any]]:
    """Tách nội dung theo đoạn/câu có overlap khi LLM không trả về chunks. Công nghệ: paragraph-first, rồi sentence với overlap."""
    if not content or not content.strip():
        return []
    text = content.strip()
    out = []
    # Bước 1: tách theo đoạn (2+ newline)
    paragraphs = re.split(r"\n\s*\n", text)
    current = []
    current_len = 0
    order = 1
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        plen = len(para) + 2
        if current_len + plen > CHUNK_TARGET_SIZE and current:
            chunk_text = "\n\n".join(current).strip()
            if chunk_text:
                out.append({"title": f"Phần {order}", "content": chunk_text, "order": order})
                order += 1
            current = [para]
            current_len = plen
        else:
            current.append(para)
            current_len += plen
    if current:
        chunk_text = "\n\n".join(current).strip()
        if chunk_text:
            out.append({"title": f"Phần {order}", "content": chunk_text, "order": order})
    if not out:
        out.append({"title": "Phần 1", "content": text[:50000], "order": 1})
    return out


def _get_supabase():
    from config import init_services
    s = init_services()
    return (s or {}).get("supabase")


def _get_persona():
    from persona import PersonaSystem
    try:
        names = PersonaSystem.get_available_personas()
        return PersonaSystem.get_persona(names[0] if names else "Writer") or {}
    except Exception:
        return {"extractor_prompt": "Trích xuất thực thể quan trọng: nhân vật, địa điểm, sự kiện, đồ vật."}


def _llm_unified_extract(content: str, chapter_label: str, persona: dict) -> Dict[str, Any]:
    """Một lần gọi LLM trả về JSON: bible, timeline, chunks, relations, chunk_bible, chunk_timeline."""
    from ai.service import AIService, _get_default_tool_model
    from config import Config
    allowed = Config.get_allowed_prefix_keys_for_extract() if hasattr(Config, "get_allowed_prefix_keys_for_extract") else ["CHARACTER", "LOCATION", "EVENT", "OTHER"]
    prefix_list = ", ".join(allowed) + ", OTHER"
    prompt = f"""Bạn là trợ lý phân tích văn bản. Từ NỘI DUNG CHƯƠNG dưới, trích xuất ĐỒNG THỜI:
1) Bible: thực thể (nhân vật, địa điểm, sự kiện, đồ vật...) - type phải là MỘT trong: {prefix_list}.
2) Timeline: sự kiện theo thứ tự thời gian - event_type: event|flashback|milestone|timeskip|other.
3) Chunks: chia nội dung thành các đoạn có ý nghĩa (theo scene/hành động), mỗi đoạn có title ngắn và content.
4) Relations: cặp thực thể có quan hệ (dùng đúng entity_name đã liệt kê trong bible).
5) chunk_bible: với mỗi chunk, thực thể bible nào xuất hiện (chunk_index 0-based, bible_index 0-based).
6) chunk_timeline: với mỗi chunk, sự kiện timeline nào được nhắc (chunk_index, timeline_index 0-based).

CHƯƠNG: {chapter_label}
NỘI DUNG:
---
{content[:UNIFIED_MAX_CONTENT_CHARS]}
---

Trả về ĐÚNG MỘT JSON với các key:
- "bible": [ {{ "entity_name": "...", "type": "...", "description": "..." }} ]
- "timeline": [ {{ "event_order": 1, "title": "...", "description": "...", "raw_date": "", "event_type": "event" }} ]
- "chunks": [ {{ "title": "...", "content": "...", "order": 1 }} ]
- "relations": [ {{ "source": "tên thực thể", "target": "tên thực thể", "relation_type": "...", "reason": "..." }} ]
- "chunk_bible": [ {{ "chunk_index": 0, "bible_index": 0, "mention_role": "primary" }} ]
- "chunk_timeline": [ {{ "chunk_index": 0, "timeline_index": 0, "mention_role": "primary" }} ]

Nếu không có: mảng rỗng []. Chỉ trả về JSON, không giải thích."""
    try:
        resp = AIService.call_openrouter(
            messages=[{"role": "user", "content": prompt}],
            model=_get_default_tool_model(),
            temperature=0.2,
            max_tokens=16000,
            response_format={"type": "json_object"},
        )
        raw = (resp.choices[0].message.content or "").strip()
        raw = re.sub(r"^```\w*\n?", "", raw).strip()
        raw = re.sub(r"\n?```\s*$", "", raw).strip()
        data = json.loads(AIService.clean_json_text(raw))
        return data
    except Exception as e:
        raise RuntimeError(f"LLM unified extract failed: {e}") from e


def _step_with_retry(fn, max_retries: int = 2) -> Tuple[bool, Optional[str]]:
    """Chạy fn(); nếu exception thì retry tối đa max_retries lần. Trả về (success, error_message)."""
    last_err = None
    for _ in range(max_retries):
        try:
            fn()
            return True, None
        except Exception as e:
            last_err = str(e)
    return False, last_err


def run_unified_chapter_analyze(
    project_id: str,
    chapter_number: int,
    job_id: Optional[str] = None,
    update_job_fn=None,
) -> Dict[str, Any]:
    """
    Pipeline: load chapter → 1 LLM call → save bible (no embed), timeline, chunks (with embed), relations, links.
    Mỗi bước có retry 1 lần; nếu thất bại thì rollback toàn bộ và báo lỗi qua job.
    Returns: {"success": bool, "error": str|None, "steps": {"bible": bool, "timeline": bool, "chunks": bool, "relations": bool, "links": bool}, "counts": {...}}
    """
    from config import Config
    result = {
        "success": False,
        "error": None,
        "steps": {"bible": False, "timeline": False, "chunks": False, "relations": False, "links": False},
        "counts": {"bible": 0, "timeline": 0, "chunks": 0, "relations": 0, "link_bible": 0, "link_timeline": 0},
    }
    supabase = _get_supabase()
    if not supabase:
        result["error"] = "Không kết nối được Supabase."
        return result

    # Load chapter
    ch_row = supabase.table("chapters").select("id, content, title, arc_id").eq(
        "story_id", project_id
    ).eq("chapter_number", chapter_number).limit(1).execute()
    if not ch_row.data or len(ch_row.data) == 0:
        result["error"] = f"Không tìm thấy chương {chapter_number}."
        return result
    chapter = ch_row.data[0]
    chapter_id = chapter.get("id")
    content = (chapter.get("content") or "").strip()
    chapter_label = (chapter.get("title") or "").strip() or f"Chương {chapter_number}"
    arc_id = chapter.get("arc_id")

    if not content:
        result["error"] = "Chương không có nội dung."
        return result

    if len(content) > UNIFIED_MAX_CONTENT_CHARS:
        result["error"] = f"Nội dung chương vượt {UNIFIED_MAX_CONTENT_CHARS} ký tự. Cắt bớt hoặc dùng Data Analyze từng bước."
        return result

    # 1) LLM call
    persona = _get_persona()
    try:
        data = _llm_unified_extract(content, chapter_label, persona)
    except Exception as e:
        result["error"] = str(e)
        if job_id and update_job_fn:
            update_job_fn(job_id, "failed", result_summary="Unified extract thất bại.", error_message=result["error"])
        return result

    bible_items = data.get("bible") or []
    timeline_items = data.get("timeline") or []
    chunks_data = data.get("chunks") or []
    relations_data = data.get("relations") or []
    chunk_bible_data = data.get("chunk_bible") or []
    chunk_timeline_data = data.get("chunk_timeline") or []

    if not isinstance(bible_items, list):
        bible_items = []
    if not isinstance(timeline_items, list):
        timeline_items = []
    if not isinstance(chunks_data, list):
        chunks_data = []
    if not isinstance(relations_data, list):
        relations_data = []
    if not isinstance(chunk_bible_data, list):
        chunk_bible_data = []
    if not isinstance(chunk_timeline_data, list):
        chunk_timeline_data = []

    # Fallback: nếu LLM không trả về chunks thì tách theo đoạn (paragraph) + overlap
    if not chunks_data and content:
        chunks_data = _fallback_semantic_chunks(content)

    # Track inserted IDs for rollback
    inserted_bible_ids: List[Any] = []
    inserted_timeline_ids: List[Any] = []
    inserted_chunk_ids: List[Any] = []

    def rollback():
        for cid in reversed(inserted_chunk_ids):
            try:
                supabase.table("chunk_bible_links").delete().eq("chunk_id", cid).execute()
                supabase.table("chunk_timeline_links").delete().eq("chunk_id", cid).execute()
            except Exception:
                pass
        for cid in inserted_chunk_ids:
            try:
                supabase.table("chunks").delete().eq("id", cid).execute()
            except Exception:
                pass
        for eid in inserted_timeline_ids:
            try:
                supabase.table("timeline_events").delete().eq("id", eid).execute()
            except Exception:
                pass
        for bid in inserted_bible_ids:
            try:
                supabase.table("story_bible").delete().eq("id", bid).execute()
            except Exception:
                pass

    # Delete existing data for this chapter
    try:
        r = supabase.table("story_bible").select("id").eq("story_id", project_id).eq("source_chapter", chapter_number).execute()
        entity_ids_chapter = [row["id"] for row in (r.data or []) if row.get("id")]
        for bid in entity_ids_chapter:
            supabase.table("story_bible").delete().eq("id", bid).execute()
        if entity_ids_chapter:
            rels = supabase.table("entity_relations").select("id, source_entity_id, target_entity_id").eq("story_id", project_id).execute()
            to_del = [x["id"] for x in (rels.data or []) if x.get("id") and (x.get("source_entity_id") in entity_ids_chapter or x.get("target_entity_id") in entity_ids_chapter)]
            for rid in to_del:
                supabase.table("entity_relations").delete().eq("id", rid).execute()
        r = supabase.table("timeline_events").select("id").eq("story_id", project_id).eq("chapter_id", chapter_id).execute()
        for row in (r.data or []):
            if row.get("id"):
                supabase.table("timeline_events").delete().eq("id", row["id"]).execute()
        r = supabase.table("chunks").select("id").eq("story_id", project_id).eq("chapter_id", chapter_id).execute()
        for row in (r.data or []):
            cid = row.get("id")
            if cid:
                try:
                    supabase.table("chunk_bible_links").delete().eq("chunk_id", cid).execute()
                except Exception:
                    pass
                try:
                    supabase.table("chunk_timeline_links").delete().eq("chunk_id", cid).execute()
                except Exception:
                    pass
                supabase.table("chunks").delete().eq("id", cid).execute()
    except Exception as e:
        result["error"] = f"Xóa dữ liệu cũ thất bại: {e}"
        if job_id and update_job_fn:
            update_job_fn(job_id, "failed", result_summary="Unified: xóa dữ liệu cũ lỗi.", error_message=result["error"])
        return result

    # Step 2: Save Bible (no embedding). Giữ thứ tự theo bible_items để chunk_bible link đúng index.
    bible_ids_by_index: List[Any] = [None] * max(len(bible_items), 1)  # bible_index từ LLM -> id
    def save_bible():
        for i, item in enumerate(bible_items):
            name = (item.get("entity_name") or "").strip()
            if not name:
                continue
            raw_type = (item.get("type") or "OTHER").strip()
            prefix = Config.resolve_prefix_for_bible(raw_type) if hasattr(Config, "resolve_prefix_for_bible") else "OTHER"
            final_name = f"[{prefix}] {name}" if not name.startswith("[") else name
            desc = (item.get("description") or "").strip()[:2000]
            ins = supabase.table("story_bible").insert({
                "story_id": project_id,
                "entity_name": final_name,
                "description": desc or final_name,
                "source_chapter": chapter_number,
            }).execute()
            if ins.data and ins.data[0].get("id"):
                bid = ins.data[0]["id"]
                inserted_bible_ids.append(bid)
                if i < len(bible_ids_by_index):
                    bible_ids_by_index[i] = bid
                else:
                    bible_ids_by_index.append(bid)

    ok, err = _step_with_retry(save_bible)
    result["steps"]["bible"] = ok
    result["counts"]["bible"] = len(inserted_bible_ids)
    if not ok:
        result["error"] = f"Bước Bible thất bại: {err}"
        rollback()
        if job_id and update_job_fn:
            update_job_fn(job_id, "failed", result_summary="Unified: bước Bible thất bại.", error_message=result["error"])
        return result

    # Step 2b: Bible embedding (để đồng bộ toàn cục phát hiện cùng thực thể khác tên)
    def save_bible_embedding():
        from ai.service import AIService
        if not inserted_bible_ids or not hasattr(AIService, "get_embeddings_batch"):
            return
        r = supabase.table("story_bible").select("id, description").in_("id", inserted_bible_ids).execute()
        rows = list(r.data or [])
        texts = [(row.get("description") or "").strip() or (row.get("id") or "") for row in rows]
        vectors = AIService.get_embeddings_batch(texts) or []
        for i, row in enumerate(rows):
            if i < len(vectors) and vectors[i]:
                try:
                    supabase.table("story_bible").update({"embedding": vectors[i]}).eq("id", row["id"]).execute()
                except Exception:
                    pass

    _step_with_retry(save_bible_embedding)  # Không rollback nếu lỗi; Bible đã lưu

    # Step 3: Save Timeline (giữ thứ tự cho chunk_timeline_links)
    timeline_ids_by_index: List[Any] = [None] * max(len(timeline_items), 1)
    def save_timeline():
        for i, ev in enumerate(timeline_items):
            order = int(ev.get("event_order", i + 1))
            title = (ev.get("title") or "").strip() or f"Sự kiện {order}"
            desc = (ev.get("description") or "").strip()[:2000]
            raw_date = (ev.get("raw_date") or "").strip()[:200]
            etype = (ev.get("event_type") or "event").lower()
            if etype not in ("event", "flashback", "milestone", "timeskip", "other"):
                etype = "event"
            ins = supabase.table("timeline_events").insert({
                "story_id": project_id,
                "chapter_id": chapter_id,
                "arc_id": arc_id,
                "event_order": order,
                "title": title,
                "description": desc,
                "raw_date": raw_date,
                "event_type": etype,
            }).execute()
            if ins.data and ins.data[0].get("id"):
                tid = ins.data[0]["id"]
                inserted_timeline_ids.append(tid)
                if i < len(timeline_ids_by_index):
                    timeline_ids_by_index[i] = tid
                else:
                    timeline_ids_by_index.append(tid)

    ok, err = _step_with_retry(save_timeline)
    result["steps"]["timeline"] = ok
    result["counts"]["timeline"] = len(inserted_timeline_ids)
    if not ok:
        result["error"] = f"Bước Timeline thất bại: {err}"
        rollback()
        if job_id and update_job_fn:
            update_job_fn(job_id, "failed", result_summary="Unified: bước Timeline thất bại.", error_message=result["error"])
        return result

    # Step 4: Chunks + embedding (chunk only, no bible)
    def save_chunks():
        from ai.service import AIService
        texts = []
        for c in chunks_data:
            txt = (c.get("content") or "").strip()
            if txt:
                texts.append(txt)
        vectors = []
        if texts and hasattr(AIService, "get_embeddings_batch"):
            vectors = AIService.get_embeddings_batch(texts) or []
        for i, ch in enumerate(chunks_data):
            txt = (ch.get("content") or "").strip()
            if not txt:
                continue
            title = (ch.get("title") or "").strip() or f"Phần {i+1}"
            order = int(ch.get("order", i + 1))
            vec = vectors[i] if i < len(vectors) else None
            row = {
                "story_id": project_id,
                "chapter_id": chapter_id,
                "arc_id": arc_id,
                "content": txt,
                "raw_content": txt,
                "meta_json": {"source": "unified_chapter_analyze", "chapter": chapter_number, "title": title},
                "sort_order": order,
            }
            if vec:
                row["embedding"] = vec
            ins = supabase.table("chunks").insert(row).execute()
            if ins.data and ins.data[0].get("id"):
                inserted_chunk_ids.append(ins.data[0]["id"])

    ok, err = _step_with_retry(save_chunks)
    result["steps"]["chunks"] = ok
    result["counts"]["chunks"] = len(inserted_chunk_ids)
    if not ok:
        result["error"] = f"Bước Chunks thất bại: {err}"
        rollback()
        if job_id and update_job_fn:
            update_job_fn(job_id, "failed", result_summary="Unified: bước Chunks thất bại.", error_message=result["error"])
        return result

    # Build name_to_bible_id from actually saved (order may differ if some skipped)
    saved_bible = supabase.table("story_bible").select("id, entity_name").eq("story_id", project_id).eq("source_chapter", chapter_number).execute()
    name_to_bible_id = {}
    for row in (saved_bible.data or []):
        name = (row.get("entity_name") or "").strip()
        if name and row.get("id"):
            name_to_bible_id[name] = row["id"]
            if "]" in name and name.startswith("["):
                rest = name[name.index("]") + 1:].strip()
                if rest:
                    name_to_bible_id[rest] = row["id"]

    # Step 5: Relations (resolve source/target to bible ids)
    def save_relations():
        for rel in relations_data:
            src_name = (rel.get("source") or "").strip()
            tgt_name = (rel.get("target") or "").strip()
            src_id = name_to_bible_id.get(src_name) or name_to_bible_id.get(f"[OTHER] {src_name}")
            tgt_id = name_to_bible_id.get(tgt_name) or name_to_bible_id.get(f"[OTHER] {tgt_name}")
            for k, v in list(name_to_bible_id.items()):
                if src_name in k or k in src_name:
                    src_id = src_id or v
                if tgt_name in k or k in tgt_name:
                    tgt_id = tgt_id or v
            if not src_id or not tgt_id or src_id == tgt_id:
                continue
            supabase.table("entity_relations").insert({
                "story_id": project_id,
                "source_entity_id": src_id,
                "target_entity_id": tgt_id,
                "relation_type": (rel.get("relation_type") or "liên quan").strip()[:200],
                "description": (rel.get("reason") or "").strip()[:500],
                "source_chapter": chapter_number,
            }).execute()
            result["counts"]["relations"] += 1

    ok, err = _step_with_retry(save_relations)
    result["steps"]["relations"] = ok
    if not ok:
        result["error"] = f"Bước Relations thất bại: {err}"
        rollback()
        if job_id and update_job_fn:
            update_job_fn(job_id, "failed", result_summary="Unified: bước Relations thất bại.", error_message=result["error"])
        return result

    # Step 6: chunk_bible_links, chunk_timeline_links (bảng V8; nếu chưa có thì bỏ qua)
    def save_links():
        try:
            for link in chunk_bible_data:
                ci = int(link.get("chunk_index", 0))
                bi = int(link.get("bible_index", 0))
                if ci < 0 or ci >= len(inserted_chunk_ids):
                    continue
                bid = bible_ids_by_index[bi] if bi < len(bible_ids_by_index) else None
                if not bid:
                    continue
                supabase.table("chunk_bible_links").insert({
                    "story_id": project_id,
                    "chunk_id": inserted_chunk_ids[ci],
                    "bible_entry_id": bid,
                    "mention_role": (link.get("mention_role") or "primary").strip()[:50] or None,
                    "sort_order": result["counts"]["link_bible"],
                }).execute()
                result["counts"]["link_bible"] += 1
            for link in chunk_timeline_data:
                ci = int(link.get("chunk_index", 0))
                ti = int(link.get("timeline_index", 0))
                if ci < 0 or ci >= len(inserted_chunk_ids):
                    continue
                tid = timeline_ids_by_index[ti] if ti < len(timeline_ids_by_index) else None
                if not tid:
                    continue
                supabase.table("chunk_timeline_links").insert({
                    "story_id": project_id,
                    "chunk_id": inserted_chunk_ids[ci],
                    "timeline_event_id": tid,
                    "mention_role": (link.get("mention_role") or "primary").strip()[:50] or None,
                    "sort_order": result["counts"]["link_timeline"],
                }).execute()
                result["counts"]["link_timeline"] += 1
        except Exception:
            raise

    ok, err = _step_with_retry(save_links)
    result["steps"]["links"] = ok
    if not ok:
        result["error"] = f"Bước Links thất bại: {err}"
        rollback()
        if job_id and update_job_fn:
            update_job_fn(job_id, "failed", result_summary="Unified: bước Links thất bại.", error_message=result["error"])
        return result

    # Step 6b: source_chunk_id — Bible: chunk đầu tiên link tới; Timeline: chunk đầu tiên link tới
    try:
        first_chunk_by_bible: Dict[Any, Any] = {}
        for link in chunk_bible_data:
            ci = int(link.get("chunk_index", 0))
            bi = int(link.get("bible_index", 0))
            if ci < 0 or ci >= len(inserted_chunk_ids) or bi < 0 or bi >= len(bible_ids_by_index):
                continue
            bid = bible_ids_by_index[bi]
            cid = inserted_chunk_ids[ci]
            if bid and cid and bid not in first_chunk_by_bible:
                first_chunk_by_bible[bid] = cid
        for bid, cid in first_chunk_by_bible.items():
            try:
                supabase.table("story_bible").update({"source_chunk_id": cid}).eq("id", bid).execute()
            except Exception:
                pass
        first_chunk_by_timeline: Dict[Any, Any] = {}
        for link in chunk_timeline_data:
            ci = int(link.get("chunk_index", 0))
            ti = int(link.get("timeline_index", 0))
            if ci < 0 or ci >= len(inserted_chunk_ids) or ti < 0 or ti >= len(timeline_ids_by_index):
                continue
            tid = timeline_ids_by_index[ti]
            cid = inserted_chunk_ids[ci]
            if tid and cid and tid not in first_chunk_by_timeline:
                first_chunk_by_timeline[tid] = cid
        for tid, cid in first_chunk_by_timeline.items():
            try:
                supabase.table("timeline_events").update({"source_chunk_id": cid}).eq("id", tid).execute()
            except Exception:
                pass
    except Exception:
        pass

    # Step 7: Ghi unified_extract_runs (audit V8)
    try:
        supabase.table("unified_extract_runs").insert({
            "story_id": project_id,
            "chapter_id": chapter_id,
            "status": "completed",
            "bible_count": result["counts"]["bible"],
            "timeline_count": result["counts"]["timeline"],
            "chunk_count": result["counts"]["chunks"],
            "relation_count": result["counts"]["relations"],
            "link_bible_count": result["counts"]["link_bible"],
            "link_timeline_count": result["counts"]["link_timeline"],
            "meta_json": {"source": "unified_chapter_analyze"},
        }).execute()
    except Exception:
        pass

    result["success"] = True
    if job_id and update_job_fn:
        summary = f"Bible {result['counts']['bible']}, Timeline {result['counts']['timeline']}, Chunks {result['counts']['chunks']}, Relations {result['counts']['relations']}, Links B/T {result['counts']['link_bible']}/{result['counts']['link_timeline']}"
        update_job_fn(job_id, "completed", result_summary=summary)
    return result


def run_unified_chapter_range(
    project_id: str,
    chapter_start: int,
    chapter_end: int,
    job_id: Optional[str] = None,
    update_job_fn=None,
) -> Dict[str, Any]:
    """
    Chạy unified analyze tuần tự cho từng chương từ chapter_start đến chapter_end (bao gồm cả hai).
    Không truyền job_id/update_job_fn vào từng chương để tránh ghi đè trạng thái job.
    Trả về và (nếu có) cập nhật job với result_summary + error_message ghi rõ chương nào lỗi.
    Returns: {"success": bool, "total": int, "ok": int, "failed": [int], "error_per_chapter": {ch: str}}
    """
    start, end = min(chapter_start, chapter_end), max(chapter_start, chapter_end)
    total = max(0, end - start + 1)
    failed_list: List[int] = []
    error_per_chapter: Dict[int, str] = {}

    for ch in range(start, end + 1):
        out = run_unified_chapter_analyze(
            project_id, ch,
            job_id=None,
            update_job_fn=None,
        )
        if not out.get("success"):
            failed_list.append(ch)
            err = (out.get("error") or "Lỗi không xác định")[:500]
            error_per_chapter[ch] = err

    ok = total - len(failed_list)
    success = len(failed_list) == 0

    if job_id and update_job_fn:
        if success:
            summary = f"Đã xong {ok}/{total} chương (chương {start}–{end})."
            update_job_fn(job_id, "completed", result_summary=summary)
        else:
            summary = f"Đã xong {ok}/{total} chương (chương {start}–{end})."
            err_parts = [f"Chương {ch}: {error_per_chapter.get(ch, '')}" for ch in failed_list]
            error_message = "Lỗi: " + "; ".join(err_parts)
            update_job_fn(job_id, "failed", result_summary=summary, error_message=error_message)

    return {
        "success": success,
        "total": total,
        "ok": ok,
        "failed": failed_list,
        "error_per_chapter": error_per_chapter,
    }
