# core/user_data_save_pipeline.py - Check logic + match quan hệ/parent với dữ liệu cũ trước khi lưu
"""Khi user (hoặc job) lưu bible, chunk, relation, timeline: bước 1 check logic (validate), bước 2 match với dữ liệu cũ (parent, FK), rồi mới lưu."""

from typing import Any, Dict, List, Optional, Tuple

# event_type cho timeline
TIMELINE_EVENT_TYPES = ("event", "flashback", "milestone", "timeskip", "other")


def _ensure_supabase(supabase=None):
    if supabase is not None:
        return supabase
    from config import init_services
    s = init_services()
    return (s or {}).get("supabase")


def validate_and_prepare_bible(
    project_id: str,
    payload: Dict[str, Any],
    supabase=None,
    *,
    existing_bible_rows: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[bool, List[str], Dict[str, Any]]:
    """
    Bước 1: Check logic (entity_name, type, parent_id tồn tại).
    Bước 2: Match parent với dữ liệu cũ (giữ parent_id nếu hợp lệ; cảnh báo trùng tên).
    Nếu truyền existing_bible_rows (list [{id, entity_name}, ...]) thì dùng thay vì query DB
    để check trùng tên — tối ưu cho unified batch (1 query thay vì N).
    Returns: (ok, errors, payload_ready).
    """
    errors: List[str] = []
    supabase = _ensure_supabase(supabase)
    if not supabase:
        return False, ["Không kết nối được dịch vụ."], {}

    # Chuẩn hóa payload (copy để không sửa input)
    out = dict(payload)
    if "story_id" not in out:
        out["story_id"] = project_id

    # --- Check logic ---
    name = (out.get("entity_name") or "").strip()
    if not name:
        errors.append("Tên thực thể (entity_name) không được để trống.")
    desc = (out.get("description") or "").strip()
    if not desc and not name:
        errors.append("Mô tả hoặc tên thực thể phải có ít nhất một.")

    parent_id = out.get("parent_id")
    if parent_id is not None:
        try:
            r = supabase.table("story_bible").select("id").eq("story_id", project_id).eq("id", parent_id).limit(1).execute()
            if not r.data or len(r.data) == 0:
                errors.append("Parent Entity không tồn tại trong Bible của project này.")
                out["parent_id"] = None  # bỏ FK không hợp lệ
        except Exception as e:
            errors.append(f"Kiểm tra parent_id thất bại: {e}")
            out["parent_id"] = None

    source_chapter = out.get("source_chapter")
    if source_chapter is not None and not isinstance(source_chapter, (int, float)):
        try:
            out["source_chapter"] = int(source_chapter)
        except (TypeError, ValueError):
            out["source_chapter"] = 0

    # --- Match / cảnh báo trùng tên (dùng existing_bible_rows nếu có để tránh N query) ---
    duplicate_warning = None
    if name and not errors:
        try:
            if existing_bible_rows is not None:
                rows = existing_bible_rows
            else:
                r = supabase.table("story_bible").select("id, entity_name").eq("story_id", project_id).execute()
                rows = r.data or []
            for row in rows:
                if row.get("id") == out.get("id"):
                    continue
                existing_name = (row.get("entity_name") or "").strip()
                if existing_name and (existing_name == name or existing_name.lower() == name.lower()):
                    duplicate_warning = f"Đã có entity trùng tên trong Bible: «{existing_name}». Đã lưu bình thường."
                    break
        except Exception:
            pass
    if duplicate_warning:
        errors.append(duplicate_warning)  # vẫn trả về ok=True; UI có thể hiển thị dạng warning

    return len([e for e in errors if "trùng tên" not in e]) == 0, errors, out


def validate_and_prepare_timeline(
    project_id: str,
    payload: Dict[str, Any],
    supabase=None,
) -> Tuple[bool, List[str], Dict[str, Any]]:
    """
    Check logic: title, event_type, event_order; chapter_id/arc_id nếu có phải tồn tại.
    Match: resolve chapter_id từ chapter_number nếu cần; arc_id từ chapter nếu cần.
    """
    errors: List[str] = []
    supabase = _ensure_supabase(supabase)
    if not supabase:
        return False, ["Không kết nối được dịch vụ."], {}

    out = dict(payload)
    if "story_id" not in out:
        out["story_id"] = project_id

    title = (out.get("title") or "").strip()
    if not title:
        errors.append("Tiêu đề sự kiện không được để trống.")
    out["title"] = title or "Sự kiện"

    etype = (out.get("event_type") or "event").strip().lower()
    if etype not in TIMELINE_EVENT_TYPES:
        etype = "event"
    out["event_type"] = etype

    try:
        out["event_order"] = int(out.get("event_order", 0))
    except (TypeError, ValueError):
        out["event_order"] = 0

    chapter_id = out.get("chapter_id")
    if chapter_id is not None:
        try:
            r = supabase.table("chapters").select("id").eq("story_id", project_id).eq("id", chapter_id).limit(1).execute()
            if not r.data or len(r.data) == 0:
                errors.append("Chapter không tồn tại trong project.")
                out["chapter_id"] = None
        except Exception as e:
            errors.append(f"Kiểm tra chapter_id thất bại: {e}")
            out["chapter_id"] = None

    arc_id = out.get("arc_id")
    if arc_id is not None and arc_id != "":
        try:
            r = supabase.table("arcs").select("id").eq("story_id", project_id).eq("id", arc_id).limit(1).execute()
            if not r.data or len(r.data) == 0:
                out["arc_id"] = None
        except Exception:
            out["arc_id"] = None

    out["description"] = (out.get("description") or "").strip()
    out["raw_date"] = (out.get("raw_date") or "").strip()[:200]

    return len(errors) == 0, errors, out


def validate_and_prepare_relation(
    project_id: str,
    payload: Dict[str, Any],
    supabase=None,
) -> Tuple[bool, List[str], Dict[str, Any]]:
    """
    Check logic: source_entity_id, target_entity_id phải tồn tại trong story_bible, khác nhau.
    Match: nếu payload có source_name/target_name (tên) thay vì id, resolve sang id từ story_bible.
    """
    errors: List[str] = []
    supabase = _ensure_supabase(supabase)
    if not supabase:
        return False, ["Không kết nối được dịch vụ."], {}

    out = dict(payload)
    if "story_id" not in out:
        out["story_id"] = project_id

    src_id = out.get("source_entity_id") or out.get("entity_id") or out.get("from_entity_id")
    tgt_id = out.get("target_entity_id") or out.get("to_entity_id")

    # Resolve by name if ids not provided
    name_to_id: Dict[str, str] = {}
    if not src_id or not tgt_id:
        try:
            r = supabase.table("story_bible").select("id, entity_name").eq("story_id", project_id).execute()
            for row in (r.data or []):
                bid = row.get("id")
                name = (row.get("entity_name") or "").strip()
                if bid and name:
                    name_to_id[name] = str(bid)
                    if "]" in name and name.startswith("["):
                        rest = name[name.index("]") + 1:].strip()
                        if rest:
                            name_to_id[rest] = str(bid)
            if not src_id and out.get("source_name"):
                src_id = name_to_id.get((out.get("source_name") or "").strip())
            if not tgt_id and out.get("target_name"):
                tgt_id = name_to_id.get((out.get("target_name") or "").strip())
        except Exception as e:
            errors.append(f"Không thể tra cứu entity: {e}")
    if src_id and isinstance(src_id, str) and not isinstance(src_id, str):
        src_id = str(src_id)
    if tgt_id and isinstance(tgt_id, str) and not isinstance(tgt_id, str):
        tgt_id = str(tgt_id)

    if not src_id:
        errors.append("Thiếu source entity (nguồn quan hệ).")
    if not tgt_id:
        errors.append("Thiếu target entity (đích quan hệ).")
    if src_id and tgt_id:
        src_id = str(src_id)
        tgt_id = str(tgt_id)
        if src_id == tgt_id:
            errors.append("Source và target không được trùng nhau.")

    if not errors and src_id and tgt_id:
        try:
            r = supabase.table("story_bible").select("id").eq("story_id", project_id).in_("id", [src_id, tgt_id]).execute()
            ids_ok = {str(row["id"]) for row in (r.data or []) if row.get("id")}
            if str(src_id) not in ids_ok:
                errors.append("Source entity không tồn tại trong Bible.")
            if str(tgt_id) not in ids_ok:
                errors.append("Target entity không tồn tại trong Bible.")
        except Exception as e:
            errors.append(f"Kiểm tra entity thất bại: {e}")

    out["source_entity_id"] = src_id
    out["target_entity_id"] = tgt_id
    out["relation_type"] = (out.get("relation_type") or out.get("relation") or "liên quan").strip()[:200] or "liên quan"
    out["description"] = (out.get("description") or "").strip()[:500]
    if "entity_id" in out:
        del out["entity_id"]
    if "from_entity_id" in out:
        del out["from_entity_id"]
    if "to_entity_id" in out:
        del out["to_entity_id"]
    if "source_name" in out:
        del out["source_name"]
    if "target_name" in out:
        del out["target_name"]

    return len(errors) == 0, errors, out


def validate_and_prepare_chunk(
    project_id: str,
    payload: Dict[str, Any],
    supabase=None,
) -> Tuple[bool, List[str], Dict[str, Any]]:
    """
    Check logic: content không trống; chapter_id/arc_id nếu có phải tồn tại.
    Match: resolve chapter_id từ chapter_number nếu payload chỉ có number.
    """
    errors: List[str] = []
    supabase = _ensure_supabase(supabase)
    if not supabase:
        return False, ["Không kết nối được dịch vụ."], {}

    out = dict(payload)
    if "story_id" not in out:
        out["story_id"] = project_id

    content = (out.get("content") or out.get("raw_content") or "").strip()
    if not content:
        errors.append("Nội dung chunk không được để trống.")
    out["content"] = content
    if "raw_content" not in out or not (out.get("raw_content") or "").strip():
        out["raw_content"] = content

    chapter_id = out.get("chapter_id")
    if chapter_id is not None:
        try:
            r = supabase.table("chapters").select("id").eq("story_id", project_id).eq("id", chapter_id).limit(1).execute()
            if not r.data or len(r.data) == 0:
                errors.append("Chapter không tồn tại trong project.")
                out["chapter_id"] = None
        except Exception as e:
            errors.append(f"Kiểm tra chapter_id thất bại: {e}")
            out["chapter_id"] = None

    arc_id = out.get("arc_id")
    if arc_id is not None and arc_id != "":
        try:
            r = supabase.table("arcs").select("id").eq("story_id", project_id).eq("id", arc_id).limit(1).execute()
            if not r.data or len(r.data) == 0:
                out["arc_id"] = None
        except Exception:
            out["arc_id"] = None

    if "sort_order" not in out or out.get("sort_order") is None:
        out["sort_order"] = 1
    try:
        out["sort_order"] = int(out["sort_order"])
    except (TypeError, ValueError):
        out["sort_order"] = 1

    return len(errors) == 0, errors, out


def run_logic_check_then_save_bible(
    project_id: str,
    payload: Dict[str, Any],
    supabase=None,
    *,
    allow_duplicate_name: bool = True,
) -> Tuple[bool, List[str], Optional[Dict[str, Any]]]:
    """
    Chạy validate_and_prepare_bible; nếu ok thì trả về payload_ready (không insert ở đây để view tự insert).
    Nếu allow_duplicate_name=False và có lỗi trùng tên thì coi là không ok.
    Returns: (ok, errors, payload_ready_or_None).
    """
    ok, errors, payload_ready = validate_and_prepare_bible(project_id, payload, supabase)
    if not allow_duplicate_name and any("trùng tên" in e for e in errors):
        return False, errors, None
    if not ok:
        return False, errors, None
    return True, [], payload_ready


def run_logic_check_then_save_timeline(
    project_id: str,
    payload: Dict[str, Any],
    supabase=None,
) -> Tuple[bool, List[str], Optional[Dict[str, Any]]]:
    """Validate + prepare timeline; trả về payload_ready để view/job insert."""
    ok, errors, payload_ready = validate_and_prepare_timeline(project_id, payload, supabase)
    if not ok:
        return False, errors, None
    return True, [], payload_ready


def run_logic_check_then_save_relation(
    project_id: str,
    payload: Dict[str, Any],
    supabase=None,
) -> Tuple[bool, List[str], Optional[Dict[str, Any]]]:
    """Validate + match relation; trả về payload_ready để insert."""
    ok, errors, payload_ready = validate_and_prepare_relation(project_id, payload, supabase)
    if not ok:
        return False, errors, None
    return True, [], payload_ready


def run_logic_check_then_save_chunk(
    project_id: str,
    payload: Dict[str, Any],
    supabase=None,
) -> Tuple[bool, List[str], Optional[Dict[str, Any]]]:
    """Validate + prepare chunk; trả về payload_ready."""
    ok, errors, payload_ready = validate_and_prepare_chunk(project_id, payload, supabase)
    if not ok:
        return False, errors, None
    return True, [], payload_ready
