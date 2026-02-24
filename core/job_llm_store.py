# core/job_llm_store.py - Lưu và lấy kết quả LLM theo job/step; dùng cho retry không gọi LLM lại.
"""Lưu llm_raw_response + parsed_result sau mỗi lần gọi LLM; retry dùng dữ liệu đã lưu."""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


def _get_supabase():
    from config import init_services
    s = init_services()
    return (s or {}).get("supabase")


def save_llm_result(
    job_id: str,
    step_type: str,
    step_key: str,
    parsed_result: Any,
    llm_raw_response: Optional[str] = None,
    input_snapshot: Optional[Dict[str, Any]] = None,
    status: str = "success",
) -> bool:
    """Lưu hoặc cập nhật một bản ghi kết quả LLM. step_key ví dụ: chapter_number (str)."""
    try:
        supabase = _get_supabase()
        if not supabase:
            return False
        now = datetime.now(tz=timezone.utc).isoformat()
        row = {
            "job_id": job_id,
            "step_type": step_type,
            "step_key": str(step_key),
            "parsed_result": parsed_result,
            "status": status,
            "updated_at": now,
        }
        if llm_raw_response is not None:
            row["llm_raw_response"] = llm_raw_response[:500_000] if llm_raw_response else None
        if input_snapshot is not None:
            row["input_snapshot"] = input_snapshot
        existing = supabase.table("job_llm_results").select("id, retry_count").eq(
            "job_id", job_id
        ).eq("step_type", step_type).eq("step_key", str(step_key)).limit(1).execute()
        if existing.data and len(existing.data) > 0:
            supabase.table("job_llm_results").update({
                "parsed_result": row["parsed_result"],
                "llm_raw_response": row.get("llm_raw_response"),
                "input_snapshot": row.get("input_snapshot", {}),
                "status": status,
                "error_message": None,
                "updated_at": now,
            }).eq("id", existing.data[0]["id"]).execute()
        else:
            row["created_at"] = now
            row["input_snapshot"] = row.get("input_snapshot") or {}
            supabase.table("job_llm_results").insert(row).execute()
        return True
    except Exception:
        return False


def get_stored_result(
    job_id: str,
    step_type: str,
    step_key: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Lấy bản ghi đã lưu. Nếu step_key None thì với unified/range lấy theo step_key (một row);
    với bible/relation/timeline/chunk mỗi job thường 1 row, step_key có thể ''.
    Trả về row (dict) hoặc None.
    """
    try:
        supabase = _get_supabase()
        if not supabase:
            return None
        q = supabase.table("job_llm_results").select("*").eq("job_id", job_id).eq("step_type", step_type)
        if step_key is not None:
            q = q.eq("step_key", str(step_key))
        q = q.order("updated_at", desc=True).limit(1)
        r = q.execute()
        if r.data and len(r.data) > 0:
            return dict(r.data[0])
        return None
    except Exception:
        return None


def get_all_stored_results_for_job(job_id: str) -> List[Dict[str, Any]]:
    """Lấy tất cả bản ghi job_llm_results của job (cho unified_chapter_range nhiều chương)."""
    try:
        supabase = _get_supabase()
        if not supabase:
            return []
        r = supabase.table("job_llm_results").select("*").eq("job_id", job_id).order(
            "step_key"
        ).execute()
        return list(r.data or [])
    except Exception:
        return []


def mark_failed(
    job_id: str,
    step_type: str,
    step_key: str,
    error_message: str,
) -> bool:
    """Đánh dấu bản ghi thất bại (sau khi bước lưu DB lỗi)."""
    try:
        supabase = _get_supabase()
        if not supabase:
            return False
        now = datetime.now(tz=timezone.utc).isoformat()
        q = supabase.table("job_llm_results").select("id").eq("job_id", job_id).eq(
            "step_type", step_type
        ).eq("step_key", str(step_key)).limit(1).execute()
        if not q.data or len(q.data) == 0:
            return False
        supabase.table("job_llm_results").update({
            "status": "failed",
            "error_message": (error_message or "")[:2000],
            "updated_at": now,
        }).eq("id", q.data[0]["id"]).execute()
        return True
    except Exception:
        return False


def increment_retry_count(job_id: str, step_type: str, step_key: str) -> bool:
    """Tăng retry_count khi user bấm Thử lại nhưng vẫn thất bại."""
    try:
        supabase = _get_supabase()
        if not supabase:
            return False
        q = supabase.table("job_llm_results").select("id, retry_count").eq("job_id", job_id).eq(
            "step_type", step_type
        ).eq("step_key", str(step_key)).limit(1).execute()
        if not q.data or len(q.data) == 0:
            return False
        new_count = int(q.data[0].get("retry_count") or 0) + 1
        now = datetime.now(tz=timezone.utc).isoformat()
        supabase.table("job_llm_results").update({
            "retry_count": new_count,
            "status": "failed",
            "updated_at": now,
        }).eq("id", q.data[0]["id"]).execute()
        return True
    except Exception:
        return False


def has_stored_result_for_retry(job_id: str, step_type: Optional[str] = None) -> bool:
    """
    Kiểm tra job có dữ liệu đã lưu để retry không (parsed_result không null).
    Nếu step_type None thì kiểm tra bất kỳ step_type nào của job.
    """
    try:
        supabase = _get_supabase()
        if not supabase:
            return False
        q = supabase.table("job_llm_results").select("id").eq("job_id", job_id).not_.is_(
            "parsed_result", "null"
        ).limit(1)
        if step_type:
            q = q.eq("step_type", step_type)
        r = q.execute()
        return bool(r.data and len(r.data) > 0)
    except Exception:
        return False
