# views/timeline_view.py - Quản lý Timeline (bảng timeline_events)
"""Tab Timeline trong Knowledge: xem, thêm, sửa, xóa sự kiện timeline."""
import streamlit as st

from config import init_services
from ai_engine import get_timeline_events
from utils.auth_manager import check_permission
from utils.cache_helpers import full_refresh
from core.user_data_save_pipeline import run_logic_check_then_save_timeline

KNOWLEDGE_PAGE_SIZE = 10


def render_timeline_tab(project_id):
    st.header("📅 Timeline")
    st.caption("Quản lý sự kiện theo thứ tự thời gian (timeline_events). Bấm **Refresh** để tải lại.")

    if not project_id:
        st.info("📁 Chọn Project trước.")
        return

    services = init_services()
    if not services:
        st.warning("Không kết nối được dịch vụ.")
        return
    supabase = services["supabase"]

    try:
        supabase.table("timeline_events").select("id").limit(1).execute()
    except Exception as e:
        st.warning("Bảng timeline_events chưa tồn tại. Chạy migration schema_v7_migration.sql trên Supabase.")
        return

    timeline_no_vec_count = 0
    try:
        r = supabase.table("timeline_events").select("id").eq("story_id", project_id).is_("embedding", "NULL").limit(1001).execute()
        timeline_no_vec_count = len(r.data or [])
        if timeline_no_vec_count > 1000:
            timeline_no_vec_count = 1001
    except Exception:
        pass
    lbl_tl = "1000+" if timeline_no_vec_count > 1000 else str(timeline_no_vec_count)
    st.caption(f"**Vector:** {lbl_tl} sự kiện chưa có embedding.")

    user_id = getattr(st.session_state.get("user"), "id", None) or ""
    user_email = getattr(st.session_state.get("user"), "email", None) or ""
    can_write = check_permission(user_id, user_email, project_id, "write")

    from core.background_jobs import is_embedding_backfill_running
    _timeline_running = is_embedding_backfill_running("timeline")
    if not _timeline_running and st.session_state.get("embedding_sync_clicked_timeline"):
        st.session_state.pop("embedding_sync_clicked_timeline", None)
    sync_timeline = _timeline_running or st.session_state.get("embedding_sync_clicked_timeline", False)
    if sync_timeline:
        st.caption("⏳ Đang đồng bộ vector (Timeline). Vui lòng đợi xong rồi bấm Refresh.")
    if st.button("🔄 Refresh", key="timeline_refresh_btn"):
        full_refresh()
    if can_write:
        if st.button("🔄 Đồng bộ vector (Timeline)", key="timeline_sync_vec_btn", disabled=(timeline_no_vec_count == 0 or sync_timeline)):
            import threading
            from core.background_jobs import run_embedding_backfill
            st.session_state["embedding_sync_clicked_timeline"] = True
            def _run():
                run_embedding_backfill(project_id, bible_limit=0, chunks_limit=0, relations_limit=0, timeline_limit=200)
            threading.Thread(target=_run, daemon=True).start()
            st.toast("Đã bắt đầu đồng bộ vector (Timeline). Bấm Refresh sau vài giây.")
            st.rerun()

    # Filter theo chương (chapter_id)
    try:
        ch_list = supabase.table("chapters").select("id, chapter_number, title").eq("story_id", project_id).order("chapter_number").execute().data or []
        tl_chapter_options = ["Tất cả"] + [f"Chương {r.get('chapter_number', '')}: {r.get('title') or ''}" for r in ch_list]
        tl_chapter_ids = [None] + [r.get("id") for r in ch_list]
    except Exception:
        tl_chapter_options = ["Tất cả"]
        tl_chapter_ids = [None]
    tl_filter_chapter_idx = st.session_state.get("timeline_filter_chapter", 0)
    tl_filter_chapter_idx = max(0, min(tl_filter_chapter_idx, len(tl_chapter_options) - 1))
    tl_filter_chapter_label = st.selectbox(
        "Chương",
        range(len(tl_chapter_options)),
        index=tl_filter_chapter_idx,
        format_func=lambda i: tl_chapter_options[i] if i < len(tl_chapter_options) else "",
        key="timeline_filter_chapter_select",
        help="Chỉ hiển thị sự kiện thuộc chương đã chọn.",
    )
    st.session_state["timeline_filter_chapter"] = tl_filter_chapter_label
    tl_filter_chapter_id = tl_chapter_ids[tl_filter_chapter_label] if tl_filter_chapter_label < len(tl_chapter_ids) else None
    if tl_filter_chapter_id is not None and st.session_state.get("timeline_filter_chapter_prev") != tl_filter_chapter_label:
        st.session_state["timeline_page"] = 1
    st.session_state["timeline_filter_chapter_prev"] = tl_filter_chapter_label

    # Phân trang ở DB (tối đa 10 mục/trang)
    page = max(1, int(st.session_state.get("timeline_page", 1)))
    try:
        count_q = supabase.table("timeline_events").select("id", count="exact").eq("story_id", project_id)
        if tl_filter_chapter_id is not None:
            count_q = count_q.eq("chapter_id", tl_filter_chapter_id)
        count_res = count_q.limit(0).execute()
        total_events = getattr(count_res, "count", None) or 0
    except Exception:
        total_events = 0
    total_pages = max(1, (total_events + KNOWLEDGE_PAGE_SIZE - 1) // KNOWLEDGE_PAGE_SIZE)
    page = max(1, min(page, total_pages))
    st.session_state["timeline_page"] = page
    offset = (page - 1) * KNOWLEDGE_PAGE_SIZE
    try:
        r_q = (
            supabase.table("timeline_events")
            .select("id, event_order, title, description, raw_date, event_type, chapter_id, arc_id, embedding")
            .eq("story_id", project_id)
            .order("event_order")
        )
        if tl_filter_chapter_id is not None:
            r_q = r_q.eq("chapter_id", tl_filter_chapter_id)
        r = r_q.range(offset, offset + KNOWLEDGE_PAGE_SIZE - 1).execute()
        events_sorted = list(r.data or [])
    except Exception:
        events_sorted = []
        total_events = 0
        total_pages = 1

    st.subheader("Danh sách sự kiện")
    if total_pages > 1:
        pcol1, pcol2, pcol3 = st.columns([1, 2, 1])
        with pcol1:
            if st.button("⬅️ Trang trước", key="tl_prev_page", disabled=(page <= 1)):
                st.session_state["timeline_page"] = max(1, page - 1)
                st.rerun()
        with pcol2:
            st.caption(f"**Trang {page} / {total_pages}** (tối đa {KNOWLEDGE_PAGE_SIZE} mục/trang, tổng {total_events} sự kiện)")
        with pcol3:
            if st.button("Trang sau ➡️", key="tl_next_page", disabled=(page >= total_pages)):
                st.session_state["timeline_page"] = min(total_pages, page + 1)
                st.rerun()

    if not events_sorted and total_events == 0:
        st.info("Chưa có sự kiện nào. Thêm mới bên dưới hoặc trích xuất từ chương trong Data Analyze → tab Timeline.")
    else:
        for i, ev in enumerate(events_sorted):
            eid = ev.get("id")
            has_embedding = bool(ev.get("embedding"))
            sync_badge = "" if has_embedding else " 🔄 Chưa embed"
            title = f"#{ev.get('event_order', i+1)} [{ev.get('event_type', 'event')}] {ev.get('title', '')}{sync_badge}"
            with st.expander(title, expanded=False):
                if not has_embedding:
                    st.caption("🔄 Chưa đồng bộ vector — bấm **Đồng bộ vector (Timeline)** trên để embed.")
                st.write("**Mô tả:**", ev.get("description") or "(trống)")
                st.write("**Thời điểm:**", ev.get("raw_date") or "(trống)")
                if can_write:
                    col_a, col_b = st.columns(2)
                    with col_a:
                        if st.button("✏️ Sửa", key=f"tl_edit_{eid}"):
                            st.session_state["tl_editing_id"] = eid
                            st.session_state["tl_edit_title"] = ev.get("title", "")
                            st.session_state["tl_edit_description"] = ev.get("description", "") or ""
                            st.session_state["tl_edit_raw_date"] = ev.get("raw_date", "") or ""
                            st.session_state["tl_edit_event_type"] = ev.get("event_type", "event")
                            st.session_state["tl_edit_event_order"] = ev.get("event_order", 0)
                    with col_b:
                        if st.button("🗑️ Xóa", key=f"tl_del_{eid}"):
                            st.session_state["tl_confirm_delete_id"] = eid

    if st.session_state.get("tl_confirm_delete_id"):
        del_id = st.session_state["tl_confirm_delete_id"]
        st.warning("Xác nhận xóa sự kiện này?")
        if st.button("✅ Xóa", key="tl_confirm_del_yes"):
            try:
                supabase.table("timeline_events").delete().eq("id", del_id).execute()
                st.session_state.pop("tl_confirm_delete_id", None)
                st.toast("Đã xóa.")
            except Exception as e:
                st.error(str(e))
        if st.button("❌ Hủy", key="tl_confirm_del_no"):
            st.session_state.pop("tl_confirm_delete_id", None)

    # --- Form sửa (khi đang edit) ---
    if st.session_state.get("tl_editing_id"):
        st.markdown("---")
        st.subheader("✏️ Chỉnh sửa sự kiện")
        edit_id = st.session_state["tl_editing_id"]
        new_title = st.text_input("Tiêu đề", value=st.session_state.get("tl_edit_title", ""), key="tl_edit_title_inp")
        new_desc = st.text_area("Mô tả", value=st.session_state.get("tl_edit_description", ""), key="tl_edit_desc_inp")
        new_date = st.text_input("Thời điểm (raw_date)", value=st.session_state.get("tl_edit_raw_date", ""), key="tl_edit_date_inp")
        new_type = st.selectbox(
            "Loại",
            ["event", "flashback", "milestone", "timeskip", "other"],
            index=["event", "flashback", "milestone", "timeskip", "other"].index(st.session_state.get("tl_edit_event_type", "event")),
            key="tl_edit_type_inp",
        )
        new_order = st.number_input("Thứ tự (event_order)", min_value=0, value=int(st.session_state.get("tl_edit_event_order", 0)), key="tl_edit_order_inp")
        c1, c2 = st.columns(2)
        with c1:
            if st.button("💾 Lưu thay đổi", key="tl_edit_save"):
                payload = {
                    "story_id": project_id,
                    "title": new_title.strip() or "Sự kiện",
                    "description": new_desc.strip(),
                    "raw_date": new_date.strip(),
                    "event_type": new_type,
                    "event_order": new_order,
                }
                ok, errs, payload_ready = run_logic_check_then_save_timeline(project_id, payload, supabase)
                if not ok:
                    st.error("Check logic lỗi:\n" + "\n".join(errs))
                else:
                    try:
                        update_fields = {k: v for k, v in payload_ready.items() if k not in ("id", "story_id")}
                        update_fields["embedding"] = None  # Chỉnh sửa tay → xóa embed để lần đồng bộ vector sau sẽ embed lại
                        supabase.table("timeline_events").update(update_fields).eq("id", edit_id).execute()
                        for k in ["tl_editing_id", "tl_edit_title", "tl_edit_description", "tl_edit_raw_date", "tl_edit_event_type", "tl_edit_event_order"]:
                            st.session_state.pop(k, None)
                        st.toast("Đã lưu.")
                    except Exception as e:
                        st.error(str(e))
        with c2:
            if st.button("❌ Hủy sửa", key="tl_edit_cancel"):
                for k in ["tl_editing_id", "tl_edit_title", "tl_edit_description", "tl_edit_raw_date", "tl_edit_event_type", "tl_edit_event_order"]:
                    st.session_state.pop(k, None)

    # --- Thêm mới ---
    st.markdown("---")
    st.subheader("➕ Thêm sự kiện mới")
    if not can_write:
        st.caption("Chỉ thành viên có quyền ghi mới thêm/sửa/xóa.")
    else:
        with st.form("tl_new_form"):
            new_title = st.text_input("Tiêu đề", key="tl_new_title")
            new_desc = st.text_area("Mô tả", key="tl_new_desc")
            new_date = st.text_input("Thời điểm (raw_date)", placeholder="vd: đầu chương 3, năm 2020", key="tl_new_date")
            new_type = st.selectbox("Loại", ["event", "flashback", "milestone", "timeskip", "other"], key="tl_new_type")
            _ev_count = len(get_timeline_events(project_id, limit=500))
            new_order = st.number_input("Thứ tự (event_order)", min_value=0, value=_ev_count + 1, key="tl_new_order")
            if st.form_submit_button("Thêm"):
                if new_title and new_title.strip():
                    payload = {
                        "story_id": project_id,
                        "event_order": new_order,
                        "title": new_title.strip(),
                        "description": (new_desc or "").strip(),
                        "raw_date": (new_date or "").strip(),
                        "event_type": new_type,
                    }
                    ok, errs, payload_ready = run_logic_check_then_save_timeline(project_id, payload, supabase)
                    if not ok:
                        st.error("Check logic lỗi:\n" + "\n".join(errs))
                    else:
                        try:
                            supabase.table("timeline_events").insert(payload_ready).execute()
                            st.toast("Đã thêm sự kiện.")
                        except Exception as e:
                            st.error(str(e))
                else:
                    st.warning("Nhập tiêu đề.")
