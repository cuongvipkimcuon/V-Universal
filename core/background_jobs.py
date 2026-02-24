# core/background_jobs.py - Background jobs (Data Analyze + Chat). "Background Jobs" tab shows status.
"""Create/update/list jobs. Workers run in thread; completion is not posted to chat (see Background Jobs tab)."""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

from core.user_data_save_pipeline import (
    run_logic_check_then_save_bible,
    run_logic_check_then_save_relation,
    run_logic_check_then_save_timeline,
    run_logic_check_then_save_chunk,
)

# Lazy init_services trong worker để tránh circular / streamlit khi import.


def create_job(
    story_id: str,
    user_id: Optional[str],
    job_type: str,
    label: str,
    payload: Optional[Dict[str, Any]] = None,
    post_to_chat: bool = True,
) -> Optional[str]:
    """Tạo bản ghi job (status=pending). Trả về job_id hoặc None nếu lỗi."""
    try:
        from config import init_services
        services = init_services()
        if not services:
            return None
        r = services["supabase"].table("background_jobs").insert({
            "story_id": story_id,
            "user_id": user_id or "",
            "job_type": job_type,
            "label": label,
            "payload": payload or {},
            "status": "pending",
            "post_to_chat": post_to_chat,
        }).execute()
        if r.data and len(r.data) > 0:
            return r.data[0].get("id")
    except Exception:
        pass
    return None


def update_job(
    job_id: str,
    status: str,
    result_summary: Optional[str] = None,
    error_message: Optional[str] = None,
) -> None:
    try:
        from config import init_services
        services = init_services()
        if not services:
            return
        payload = {"status": status}
        if status in ("completed", "failed"):
            payload["completed_at"] = datetime.now(tz=timezone.utc).isoformat()
        if status == "running":
            payload["started_at"] = datetime.now(tz=timezone.utc).isoformat()
        if result_summary is not None:
            payload["result_summary"] = result_summary
        if error_message is not None:
            payload["error_message"] = error_message[:2000]
        services["supabase"].table("background_jobs").update(payload).eq("id", job_id).execute()
    except Exception:
        pass


def list_jobs(
    story_id: str,
    status_filter: Optional[str] = None,
    limit: int = 80,
) -> List[Dict[str, Any]]:
    """Lấy danh sách job của dự án, mới nhất trước. status_filter: pending | running | completed | failed hoặc None (tất cả)."""
    try:
        from config import init_services
        services = init_services()
        if not services:
            return []
        q = services["supabase"].table("background_jobs").select("*").eq("story_id", story_id).order("created_at", desc=True).limit(limit)
        if status_filter:
            q = q.eq("status", status_filter)
        r = q.execute()
        return list(r.data or [])
    except Exception:
        return []


def _post_completion_to_chat(project_id: str, user_id: Optional[str], label: str, success: bool, result_summary: Optional[str], error_message: Optional[str]) -> None:
    """Ghi tin hoàn thành vào chat_history để V Work hiện toast (metadata data_operation_completion)."""
    try:
        from config import init_services
        services = init_services()
        if not services:
            return
        now_iso = datetime.now(tz=timezone.utc).isoformat()
        now_display = datetime.now().strftime("%d/%m/%Y %H:%M")
        if success:
            content = f"✅ **{label}** — Hoàn thành. {result_summary or ''} Thời gian: {now_display}."
        else:
            content = f"⚠️ **{label}** — Không hoàn thành. {error_message or result_summary or 'Lỗi không xác định.'} Thời gian: {now_display}."
        services["supabase"].table("chat_history").insert({
            "story_id": project_id,
            "user_id": str(user_id) if user_id else None,
            "role": "model",
            "content": content,
            "created_at": now_iso,
            "metadata": {"data_operation_completion": True, "success": success},
        }).execute()
    except Exception:
        pass


def run_job_worker(job_id: str) -> None:
    """
    Chạy trong thread: lấy job, set status=running, gọi worker theo job_type, cập nhật completed/failed, nếu post_to_chat thì ghi chat.
    job_type: data_analyze_bible | data_analyze_relation | data_analyze_timeline | data_analyze_chunk | data_operation_batch.
    """
    from config import init_services
    services = init_services()
    if not services:
        return
    supabase = services["supabase"]
    try:
        r = supabase.table("background_jobs").select("*").eq("id", job_id).limit(1).execute()
        if not r.data or len(r.data) == 0:
            return
        job = r.data[0]
        story_id = job.get("story_id")
        user_id = job.get("user_id") or None
        job_type = (job.get("job_type") or "").strip()
        label = (job.get("label") or "Tác vụ").strip()
        payload = job.get("payload") or {}
        post_to_chat = bool(job.get("post_to_chat", True))

        update_job(job_id, "running")

        if job_type == "data_operation_batch":
            from core.data_operation_jobs import run_data_operations_batch
            steps = payload.get("steps") or []
            run_data_operations_batch(
                story_id, user_id, steps,
                payload.get("user_request") or label,
                job_id=job_id,
            )
        elif job_type == "data_analyze_bible":
            _worker_data_analyze_bible(job_id, story_id, user_id, label, payload, post_to_chat, supabase)
        elif job_type == "data_analyze_relation":
            _worker_data_analyze_relation(job_id, story_id, user_id, label, payload, post_to_chat, supabase)
        elif job_type == "data_analyze_timeline":
            _worker_data_analyze_timeline(job_id, story_id, user_id, label, payload, post_to_chat, supabase)
        elif job_type == "data_analyze_chunk":
            _worker_data_analyze_chunk(job_id, story_id, user_id, label, payload, post_to_chat, supabase)
        elif job_type == "unified_chapter_analyze":
            chapter_number = int(payload.get("chapter_number", 0))
            from core.unified_chapter_analyze import run_unified_chapter_analyze
            out = run_unified_chapter_analyze(
                story_id,
                chapter_number,
                job_id=job_id,
                update_job_fn=update_job,
            )
            if not out.get("success") and not out.get("error"):
                update_job(job_id, "failed", error_message="Unified analyze thất bại (không rõ lỗi).")
        elif job_type == "unified_chapter_range":
            chapter_start = int(payload.get("chapter_start") or payload.get("chapter_range", [0, 0])[0])
            chapter_end = int(payload.get("chapter_end") or (payload.get("chapter_range") or [0, 0])[1])
            from core.unified_chapter_analyze import run_unified_chapter_range
            out = run_unified_chapter_range(
                story_id,
                chapter_start,
                chapter_end,
                job_id=job_id,
                update_job_fn=update_job,
            )
            if post_to_chat:
                summary = f"Đã xong {out.get('ok', 0)}/{out.get('total', 0)} chương."
                if out.get("failed"):
                    err_detail = "; ".join([f"Chương {ch}" for ch in out.get("failed", [])])
                    _post_completion_to_chat(story_id, user_id, label, False, summary, f"Lỗi: {err_detail}")
                else:
                    _post_completion_to_chat(story_id, user_id, label, True, summary, None)
        elif job_type == "global_data_sync":
            from core.global_data_sync import run_global_data_sync
            out = run_global_data_sync(
                project_id=story_id,
                job_id=job_id,
                update_job_fn=update_job,
            )
            if out.get("success"):
                if post_to_chat:
                    fixed = out.get("fixed") or {}
                    msg = (
                        f"Dọn rác: {fixed.get('chunk_bible_links_deleted', 0)} link bible, {fixed.get('chunk_timeline_links_deleted', 0)} link timeline, {fixed.get('entity_relations_deleted', 0)} relation. "
                        f"Bible: {fixed.get('bible_parent_id_updated', 0)} parent (tên), {fixed.get('bible_parent_id_by_embedding', 0)} parent (embedding). "
                        f"Relation: {fixed.get('entity_relations_normalized', 0)} chuẩn hóa, {fixed.get('entity_relations_deduped', 0)} gộp trùng; "
                        f"CBL: {fixed.get('chunk_bible_links_normalized', 0)} chuẩn hóa, {fixed.get('chunk_bible_links_deduped', 0)} gộp trùng; "
                        f"source_chapter: {fixed.get('entity_relations_source_chapter_updated', 0)}."
                    )
                    _post_completion_to_chat(story_id, user_id, label, True, msg, None)
            else:
                if post_to_chat:
                    _post_completion_to_chat(story_id, user_id, label, False, None, out.get("error") or "Lỗi không xác định.")
        else:
            update_job(job_id, "failed", error_message=f"job_type không hỗ trợ: {job_type}")
            if post_to_chat:
                _post_completion_to_chat(story_id, user_id, label, False, None, f"job_type không hỗ trợ: {job_type}")
    except Exception as e:
        err = str(e)[:1000]
        update_job(job_id, "failed", error_message=err)
        try:
            r = supabase.table("background_jobs").select("story_id, user_id, label, post_to_chat").eq("id", job_id).limit(1).execute()
            if r.data and r.data[0].get("post_to_chat"):
                _post_completion_to_chat(
                    r.data[0].get("story_id"), r.data[0].get("user_id"), r.data[0].get("label", "Tác vụ"), False, None, err
                )
        except Exception:
            pass


def _worker_data_analyze_bible(
    job_id: str, project_id: str, user_id: Optional[str], label: str, payload: Dict, post_to_chat: bool, supabase,
) -> None:
    from config import Config
    from persona import PersonaSystem
    from views.data_analyze import _run_extract_on_content, _get_existing_bible_entity_names_for_chapter

    chap_num = int(payload.get("chapter_number", 0))
    persona_key = payload.get("persona_key", "Writer")
    exclude_existing = bool(payload.get("exclude_existing", False))

    ch_row = supabase.table("chapters").select("id, content, title").eq("story_id", project_id).eq("chapter_number", chap_num).limit(1).execute()
    if not ch_row.data:
        update_job(job_id, "failed", error_message="Không tìm thấy chương")
        if post_to_chat:
            _post_completion_to_chat(project_id, user_id, label, False, None, "Không tìm thấy chương")
        return
    content = (ch_row.data[0].get("content") or "").strip()
    if not content:
        update_job(job_id, "failed", error_message="Chương không có nội dung")
        if post_to_chat:
            _post_completion_to_chat(project_id, user_id, label, False, None, "Chương không có nội dung")
        return

    ext_persona = PersonaSystem.get_persona(persona_key) or {}
    if not exclude_existing:
        existing = supabase.table("story_bible").select("id").eq("story_id", project_id).eq("source_chapter", chap_num).execute()
        if existing.data:
            ids = [r["id"] for r in existing.data if r.get("id")]
            if ids:
                supabase.table("story_bible").delete().in_("id", ids).execute()
    unique_items = _run_extract_on_content(content, ext_persona, project_id, chap_num, exclude_existing=exclude_existing, supabase=supabase)
    if not unique_items:
        update_job(job_id, "completed", result_summary="Không tìm thấy thực thể mới.")
        if post_to_chat:
            _post_completion_to_chat(project_id, user_id, label, True, "Không tìm thấy thực thể mới.", None)
        return
    allowed_keys = Config.get_allowed_prefix_keys_for_extract()
    rows_to_save = []
    for item in unique_items:
        desc = (item.get("description") or "").strip()
        raw_name = item.get("entity_name", "Unknown")
        raw_type_str = (item.get("type") or "OTHER").strip()
        prefix_key = Config.resolve_prefix_for_bible(raw_type_str)
        final_name = f"[{prefix_key}] {raw_name}" if not raw_name.startswith("[") else raw_name
        if desc:
            rows_to_save.append({"final_name": final_name, "description": desc})
    if not rows_to_save:
        update_job(job_id, "completed", result_summary="Không có mục nào hợp lệ để lưu.")
        if post_to_chat:
            _post_completion_to_chat(project_id, user_id, label, True, "Không có mục nào hợp lệ.", None)
        return
    count = 0
    for row in rows_to_save:
        payload = {
            "story_id": project_id,
            "entity_name": row["final_name"],
            "description": row["description"],
            "source_chapter": chap_num,
        }
        ok, _, payload_ready = run_logic_check_then_save_bible(project_id, payload, supabase)
        if not ok or not payload_ready:
            continue
        supabase.table("story_bible").insert(payload_ready).execute()
        count += 1
    summary = f"Đã lưu {count} mục Bible."
    update_job(job_id, "completed", result_summary=summary)
    if post_to_chat:
        _post_completion_to_chat(project_id, user_id, label, True, summary, None)


def _worker_data_analyze_relation(
    job_id: str, project_id: str, user_id: Optional[str], label: str, payload: Dict, post_to_chat: bool, supabase,
) -> None:
    from views.data_analyze import suggest_relations, _get_entity_ids_for_chapter

    chap_num = int(payload.get("chapter_number", 0))
    only_new = bool(payload.get("only_new", False))
    ch_row = supabase.table("chapters").select("id, content").eq("story_id", project_id).eq("chapter_number", chap_num).limit(1).execute()
    if not ch_row.data:
        update_job(job_id, "failed", error_message="Không tìm thấy chương")
        if post_to_chat:
            _post_completion_to_chat(project_id, user_id, label, False, None, "Không tìm thấy chương")
        return
    content = (ch_row.data[0].get("content") or "").strip()
    if not content:
        update_job(job_id, "failed", error_message="Chương không có nội dung")
        if post_to_chat:
            _post_completion_to_chat(project_id, user_id, label, False, None, "Chương không có nội dung")
        return
    entity_ids = set(_get_entity_ids_for_chapter(project_id, chap_num, supabase))
    existing_set = set()
    if only_new:
        rels_exist = supabase.table("entity_relations").select("source_entity_id, target_entity_id, relation_type").eq("story_id", project_id).execute()
        for r in (rels_exist.data or []):
            s, t = r.get("source_entity_id"), r.get("target_entity_id")
            if s and t:
                existing_set.add((s, t, (r.get("relation_type") or "").strip()))
    else:
        if entity_ids:
            rels_exist = supabase.table("entity_relations").select("id, source_entity_id, target_entity_id").eq("story_id", project_id).execute()
            ids_to_del = [r["id"] for r in (rels_exist.data or []) if r.get("id") and (r.get("source_entity_id") in entity_ids or r.get("target_entity_id") in entity_ids)]
            if ids_to_del:
                supabase.table("entity_relations").delete().in_("id", ids_to_del).execute()
    rels = suggest_relations(content, project_id)
    saved = 0
    for item in (rels or []):
        if only_new and item.get("kind") == "relation":
            s, t = item.get("source_entity_id"), item.get("target_entity_id")
            key = (s, t, (item.get("relation_type") or "").strip())
            if key in existing_set:
                continue
        try:
            if item.get("kind") == "relation":
                payload = {
                    "source_entity_id": item["source_entity_id"],
                    "target_entity_id": item["target_entity_id"],
                    "relation_type": item.get("relation_type", "liên quan"),
                    "description": item.get("description", "") or "",
                    "story_id": project_id,
                }
                ok, _, payload_ready = run_logic_check_then_save_relation(project_id, payload, supabase)
                if ok and payload_ready:
                    supabase.table("entity_relations").insert(payload_ready).execute()
                    saved += 1
            else:
                supabase.table("story_bible").update({"parent_id": item["parent_entity_id"]}).eq("id", item["entity_id"]).execute()
                saved += 1
        except Exception:
            pass
    summary = f"Đã lưu {saved} quan hệ / parent."
    update_job(job_id, "completed", result_summary=summary)
    if post_to_chat:
        _post_completion_to_chat(project_id, user_id, label, True, summary, None)


def _worker_data_analyze_timeline(
    job_id: str, project_id: str, user_id: Optional[str], label: str, payload: Dict, post_to_chat: bool, supabase,
) -> None:
    from ai_engine import extract_timeline_events_from_content

    chap_num = int(payload.get("chapter_number", 0))
    chapter_label = payload.get("chapter_label", "") or f"Chương {chap_num}"
    ch_row = supabase.table("chapters").select("id, content").eq("story_id", project_id).eq("chapter_number", chap_num).limit(1).execute()
    if not ch_row.data:
        update_job(job_id, "failed", error_message="Không tìm thấy chương")
        if post_to_chat:
            _post_completion_to_chat(project_id, user_id, label, False, None, "Không tìm thấy chương")
        return
    content = (ch_row.data[0].get("content") or "").strip()
    chapter_id = ch_row.data[0].get("id")
    if not content:
        update_job(job_id, "failed", error_message="Chương không có nội dung")
        if post_to_chat:
            _post_completion_to_chat(project_id, user_id, label, False, None, "Chương không có nội dung")
        return
    append_only = bool(payload.get("append_only", False))
    if not append_only:
        old = supabase.table("timeline_events").select("id").eq("story_id", project_id).eq("chapter_id", chapter_id).execute()
        if old.data:
            ids = [r["id"] for r in old.data if r.get("id")]
            if ids:
                supabase.table("timeline_events").delete().in_("id", ids).execute()
    events = extract_timeline_events_from_content(content, chapter_label)
    base_order = 0
    if append_only:
        r = supabase.table("timeline_events").select("event_order").eq("story_id", project_id).order("event_order", desc=True).limit(1).execute()
        if r.data and r.data[0].get("event_order") is not None:
            base_order = int(r.data[0]["event_order"]) + 1
    saved = 0
    for ev in (events or []):
        order_val = int(ev.get("event_order", 0)) if not append_only else (base_order + saved)
        payload_ev = {
            "story_id": project_id,
            "chapter_id": chapter_id,
            "event_order": order_val,
            "title": (ev.get("title") or "").strip() or "Sự kiện",
            "description": (ev.get("description") or "").strip(),
            "raw_date": (ev.get("raw_date") or "").strip(),
            "event_type": ev.get("event_type", "event"),
        }
        ok, _, payload_ready = run_logic_check_then_save_timeline(project_id, payload_ev, supabase)
        if not ok or not payload_ready:
            continue
        try:
            supabase.table("timeline_events").insert(payload_ready).execute()
            saved += 1
        except Exception:
            pass
    summary = f"Đã lưu {saved} sự kiện timeline."
    update_job(job_id, "completed", result_summary=summary)
    if post_to_chat:
        _post_completion_to_chat(project_id, user_id, label, True, summary, None)


def _worker_data_analyze_chunk(
    job_id: str, project_id: str, user_id: Optional[str], label: str, payload: Dict, post_to_chat: bool, supabase,
) -> None:
    from ai_engine import analyze_split_strategy, execute_split_logic

    chap_num = int(payload.get("chapter_number", 0))
    ch_row = supabase.table("chapters").select("id, content, arc_id").eq("story_id", project_id).eq("chapter_number", chap_num).limit(1).execute()
    if not ch_row.data:
        update_job(job_id, "failed", error_message="Không tìm thấy chương")
        if post_to_chat:
            _post_completion_to_chat(project_id, user_id, label, False, None, "Không tìm thấy chương")
        return
    content = (ch_row.data[0].get("content") or "").strip()
    chapter_id = ch_row.data[0].get("id")
    arc_id = ch_row.data[0].get("arc_id")
    if not content:
        update_job(job_id, "failed", error_message="Chương không có nội dung")
        if post_to_chat:
            _post_completion_to_chat(project_id, user_id, label, False, None, "Chương không có nội dung")
        return
    strategy = analyze_split_strategy(content, file_type="story", context_hint="Đoạn văn có ý nghĩa")
    chunks_list = execute_split_logic(content, strategy.get("split_type", "by_length"), strategy.get("split_value", "2000"))
    if not chunks_list:
        chunks_list = execute_split_logic(content, "by_length", "2000")
    edited = [{"title": c.get("title", ""), "content": (c.get("content") or "").strip(), "order": c.get("order", i + 1)} for i, c in enumerate(chunks_list)]
    append_only = bool(payload.get("append_only", False))
    base_sort = 0
    if not append_only:
        old = supabase.table("chunks").select("id").eq("story_id", project_id).eq("chapter_id", chapter_id).execute()
        if old.data:
            ids = [r["id"] for r in old.data if r.get("id")]
            if ids:
                supabase.table("chunks").delete().in_("id", ids).execute()
    else:
        r = supabase.table("chunks").select("sort_order").eq("story_id", project_id).order("sort_order", desc=True).limit(1).execute()
        if r.data and r.data[0].get("sort_order") is not None:
            base_sort = int(r.data[0]["sort_order"]) + 1
    saved = 0
    for idx, chk in enumerate(edited):
        txt = chk.get("content", "").strip()
        if not txt:
            continue
        sort_val = chk.get("order", idx + 1) if not append_only else (base_sort + saved)
        row = {
            "story_id": project_id,
            "chapter_id": chapter_id,
            "arc_id": arc_id,
            "content": txt,
            "raw_content": txt,
            "meta_json": {"source": "data_analyze", "chapter": chap_num, "title": chk.get("title", "")},
            "sort_order": sort_val,
        }
        ok, _, payload_ready = run_logic_check_then_save_chunk(project_id, row, supabase)
        if not ok or not payload_ready:
            continue
        try:
            supabase.table("chunks").insert(payload_ready).execute()
            saved += 1
        except Exception:
            pass
    summary = f"Đã lưu {saved} chunks."
    update_job(job_id, "completed", result_summary=summary)
    if post_to_chat:
        _post_completion_to_chat(project_id, user_id, label, True, summary, None)


# Cờ riêng từng loại: tránh tràn/trùng trong từng bảng (Bible, Chunks, Relations, Timeline).
_embedding_backfill_bible_running = False
_embedding_backfill_chunks_running = False
_embedding_backfill_relations_running = False
_embedding_backfill_timeline_running = False
_embedding_backfill_semantic_running = False

EMBEDDING_KINDS = ("bible", "chunks", "relations", "timeline", "semantic_intent")


def is_embedding_backfill_running(kind: Optional[str] = None) -> bool:
    """
    True nếu đang chạy backfill embedding.
    kind: None = bất kỳ loại nào đang chạy; "bible"|"chunks"|"relations"|"timeline"|"semantic_intent" = chỉ loại đó.
    """
    if kind is None:
        return (
            _embedding_backfill_bible_running
            or _embedding_backfill_chunks_running
            or _embedding_backfill_relations_running
            or _embedding_backfill_timeline_running
            or _embedding_backfill_semantic_running
        )
    if kind == "bible":
        return _embedding_backfill_bible_running
    if kind == "chunks":
        return _embedding_backfill_chunks_running
    if kind == "relations":
        return _embedding_backfill_relations_running
    if kind == "timeline":
        return _embedding_backfill_timeline_running
    if kind == "semantic_intent":
        return _embedding_backfill_semantic_running
    return False


# Batch size cho gọi RPC bulk embedding (V8.6); fallback update từng dòng nếu RPC không có.
_EMBEDDING_BULK_BATCH = 100


def _bulk_update_embeddings(supabase, rpc_name: str, updates: List[Dict[str, Any]]) -> int:
    """Gọi RPC bulk update embedding; trả về số bản ghi đã cập nhật. Nếu RPC lỗi thì fallback update từng dòng."""
    if not updates:
        return 0
    try:
        for start in range(0, len(updates), _EMBEDDING_BULK_BATCH):
            batch = updates[start : start + _EMBEDDING_BULK_BATCH]
            supabase.rpc(rpc_name, {"updates": batch}).execute()
        return len(updates)
    except Exception as e:
        logger.warning("RPC %s failed: %s", rpc_name, e)
        return 0


def _embedding_value_for_db(vec: Any) -> Any:
    """Chuẩn hóa embedding để ghi DB: PostgREST/cột vector thường cần chuỗi '[x,y,z]' thay vì list."""
    if vec is None:
        return None
    if isinstance(vec, (list, tuple)):
        return "[" + ",".join(str(float(x)) for x in vec) + "]"
    return vec


def _fallback_update_embeddings_one_by_one(supabase, table: str, id_key: str, rows: List[Dict], vectors: List[Any]) -> int:
    """Update embedding từng dòng (fallback khi không có RPC). Gửi embedding dạng chuỗi '[0.1,0.2,...]' để cột vector nhận đúng."""
    count = 0
    for i, row in enumerate(rows):
        if i < len(vectors) and vectors[i]:
            try:
                emb = _embedding_value_for_db(vectors[i])
                supabase.table(table).update({"embedding": emb}).eq(id_key, row["id"]).execute()
                count += 1
            except Exception as e:
                logger.warning("Fallback embedding update failed %s id=%s: %s", table, row.get("id"), e)
    return count


def run_embedding_backfill(
    project_id: str,
    bible_limit: int = 50,
    chunks_limit: int = 50,
    relations_limit: int = 0,
    timeline_limit: int = 0,
) -> Dict[str, int]:
    """
    Tìm các bảng có embedding null, gọi API embedding batch, cập nhật DB (RPC bulk hoặc từng dòng).
    Trả về {"bible_updated": n, "chunks_updated": m, "relations_updated": r, "timeline_updated": t}.
    Mỗi loại (bible/chunks/relations/timeline) có cờ riêng: chỉ chặn trùng trong cùng bảng.
    """
    global _embedding_backfill_bible_running, _embedding_backfill_chunks_running
    global _embedding_backfill_relations_running, _embedding_backfill_timeline_running
    out = {"bible_updated": 0, "chunks_updated": 0, "relations_updated": 0, "timeline_updated": 0}
    if not project_id:
        return out
    if bible_limit > 0 and _embedding_backfill_bible_running:
        return out
    if chunks_limit > 0 and _embedding_backfill_chunks_running:
        return out
    if relations_limit > 0 and _embedding_backfill_relations_running:
        return out
    if timeline_limit > 0 and _embedding_backfill_timeline_running:
        return out
    try:
        from config import init_services
        from ai_engine import AIService
        services = init_services()
        if not services:
            return out
        supabase = services["supabase"]
    except Exception:
        return out
    if bible_limit > 0:
        _embedding_backfill_bible_running = True
    if chunks_limit > 0:
        _embedding_backfill_chunks_running = True
    if relations_limit > 0:
        _embedding_backfill_relations_running = True
    if timeline_limit > 0:
        _embedding_backfill_timeline_running = True
    try:
        try:
            r = supabase.table("story_bible").select("id, entity_name, description").eq("story_id", project_id).is_("embedding", "NULL").limit(bible_limit).execute()
            rows_bible = list(r.data or [])
            if rows_bible:
                texts_bible = []
                for row in rows_bible:
                    name = (row.get("entity_name") or "").strip()
                    desc = (row.get("description") or "").strip()
                    texts_bible.append(f"{name}: {desc}" if name and desc else (desc or name or ""))
                vectors_bible = AIService.get_embeddings_batch(texts_bible) if hasattr(AIService, "get_embeddings_batch") else []
                payloads = [{"id": str(row["id"]), "embedding": vectors_bible[i]} for i, row in enumerate(rows_bible) if i < len(vectors_bible) and vectors_bible[i]]
                out["bible_updated"] = _bulk_update_embeddings(supabase, "bulk_update_story_bible_embeddings", payloads)
                if out["bible_updated"] == 0 and payloads:
                    out["bible_updated"] = _fallback_update_embeddings_one_by_one(supabase, "story_bible", "id", rows_bible, vectors_bible)
        except Exception:
            pass
        try:
            r = supabase.table("chunks").select("id, content").eq("story_id", project_id).is_("embedding", "NULL").limit(chunks_limit).execute()
            rows_chunks = list(r.data or [])
            if rows_chunks:
                texts_chunks = [(row.get("content") or "").strip() or "" for row in rows_chunks]
                vectors_chunks = AIService.get_embeddings_batch(texts_chunks) if hasattr(AIService, "get_embeddings_batch") else []
                payloads = [{"id": str(row["id"]), "embedding": vectors_chunks[i]} for i, row in enumerate(rows_chunks) if i < len(vectors_chunks) and vectors_chunks[i]]
                out["chunks_updated"] = _bulk_update_embeddings(supabase, "bulk_update_chunks_embeddings", payloads)
                if out["chunks_updated"] == 0 and payloads:
                    out["chunks_updated"] = _fallback_update_embeddings_one_by_one(supabase, "chunks", "id", rows_chunks, vectors_chunks)
        except Exception:
            pass
        if relations_limit > 0:
            try:
                rels = supabase.table("entity_relations").select("id, source_entity_id, target_entity_id, relation_type, description").eq("story_id", project_id).is_("embedding", "NULL").limit(relations_limit).execute()
                rows_rel = list(rels.data or [])
                if rows_rel:
                    bible_ids = set()
                    for row in rows_rel:
                        if row.get("source_entity_id"):
                            bible_ids.add(row["source_entity_id"])
                        if row.get("target_entity_id"):
                            bible_ids.add(row["target_entity_id"])
                    id_to_name = {}
                    if bible_ids:
                        b = supabase.table("story_bible").select("id, entity_name").in_("id", list(bible_ids)).execute()
                        for x in (b.data or []):
                            if x.get("id"):
                                id_to_name[x["id"]] = (x.get("entity_name") or "").strip()
                    texts_rel = []
                    for row in rows_rel:
                        src = id_to_name.get(row.get("source_entity_id"), "")
                        tgt = id_to_name.get(row.get("target_entity_id"), "")
                        rtype = (row.get("relation_type") or "").strip()
                        desc = (row.get("description") or "").strip()
                        texts_rel.append(f"{src} {rtype} {tgt} {desc}".strip())
                    vectors_rel = AIService.get_embeddings_batch(texts_rel) if hasattr(AIService, "get_embeddings_batch") else []
                    payloads = [{"id": str(row["id"]), "embedding": vectors_rel[i]} for i, row in enumerate(rows_rel) if i < len(vectors_rel) and vectors_rel[i]]
                    out["relations_updated"] = _bulk_update_embeddings(supabase, "bulk_update_entity_relations_embeddings", payloads)
                    if out["relations_updated"] == 0 and payloads:
                        out["relations_updated"] = _fallback_update_embeddings_one_by_one(supabase, "entity_relations", "id", rows_rel, vectors_rel)
            except Exception:
                pass
        if timeline_limit > 0:
            try:
                r = supabase.table("timeline_events").select("id, title, description, raw_date").eq("story_id", project_id).is_("embedding", "NULL").limit(timeline_limit).execute()
                rows_tl = list(r.data or [])
                if rows_tl:
                    texts_tl = []
                    for row in rows_tl:
                        title = (row.get("title") or "").strip()
                        desc = (row.get("description") or "").strip()
                        raw = (row.get("raw_date") or "").strip()
                        texts_tl.append(f"{title} {desc} {raw}".strip())
                    vectors_tl = AIService.get_embeddings_batch(texts_tl) if hasattr(AIService, "get_embeddings_batch") else []
                    payloads = [{"id": str(row["id"]), "embedding": vectors_tl[i]} for i, row in enumerate(rows_tl) if i < len(vectors_tl) and vectors_tl[i]]
                    out["timeline_updated"] = _bulk_update_embeddings(supabase, "bulk_update_timeline_events_embeddings", payloads)
                    if out["timeline_updated"] == 0 and payloads:
                        out["timeline_updated"] = _fallback_update_embeddings_one_by_one(supabase, "timeline_events", "id", rows_tl, vectors_tl)
            except Exception:
                pass
    finally:
        if bible_limit > 0:
            _embedding_backfill_bible_running = False
        if chunks_limit > 0:
            _embedding_backfill_chunks_running = False
        if relations_limit > 0:
            _embedding_backfill_relations_running = False
        if timeline_limit > 0:
            _embedding_backfill_timeline_running = False
    return out


def run_semantic_intent_embedding_backfill(project_id: str, limit: int = 200) -> int:
    """
    Đồng bộ vector cho semantic_intent: embed theo question_sample (câu hỏi), ghi vào cột embedding.
    Trả về số bản ghi đã cập nhật.
    """
    global _embedding_backfill_semantic_running
    if not project_id:
        return 0
    if _embedding_backfill_semantic_running:
        return 0
    try:
        from config import init_services
        from ai_engine import AIService
        services = init_services()
        if not services:
            return 0
        supabase = services["supabase"]
    except Exception:
        return 0
    _embedding_backfill_semantic_running = True
    updated = 0
    try:
        r = supabase.table("semantic_intent").select("id, question_sample").eq("story_id", project_id).is_("embedding", "NULL").limit(limit).execute()
        rows = list(r.data or [])
        if not rows:
            return 0
        texts = [(row.get("question_sample") or "").strip() or "" for row in rows]
        vectors = AIService.get_embeddings_batch(texts) if hasattr(AIService, "get_embeddings_batch") else []
        if not vectors:
            for t in texts:
                v = AIService.get_embedding(t) if t else None
                vectors.append(v)
        updated = _fallback_update_embeddings_one_by_one(supabase, "semantic_intent", "id", rows, vectors)
    except Exception as e:
        logger.warning("run_semantic_intent_embedding_backfill failed: %s", e)
    finally:
        _embedding_backfill_semantic_running = False
    return updated
