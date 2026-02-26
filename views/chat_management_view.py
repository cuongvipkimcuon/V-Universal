# views/chat_management_view.py - Quản lý Bible entries [CHAT] (Auto Crystallize)
"""Tab quản lý [CHAT]: xem, sửa nội dung, xóa, Archive/Unarchive. Không add tay - chỉ Auto Crystallize tạo."""
import streamlit as st

from config import init_services
from ai_engine import AIService
from utils.auth_manager import check_permission
from utils.cache_helpers import get_bible_list_cached, invalidate_cache
from core.background_jobs import run_crystallize_embedding_backfill, is_embedding_backfill_running

KNOWLEDGE_PAGE_SIZE = 10


def render_chat_management_tab(project_id, persona):
    st.header("💬 Chat Knowledge")
    st.caption("Điểm nhớ từ hội thoại (Auto Crystallize). Sửa, xóa, Archive (đã archive: không đưa vào context, chỉ Unarchive).")

    if not project_id:
        st.info("📁 Chọn Project trước.")
        return

    st.session_state.setdefault("update_trigger", 0)
    services = init_services()
    if not services:
        st.warning("Không kết nối được dịch vụ.")
        return
    supabase = services["supabase"]

    # Thống kê embedding cho Chat Memory (chat_crystallize_entries) và nút đồng bộ vector riêng
    try:
        r_all = (
            supabase.table("chat_crystallize_entries")
            .select("id")
            .eq("story_id", project_id)
            .execute()
        )
        total_mem = len(r_all.data or [])
        r_null = (
            supabase.table("chat_crystallize_entries")
            .select("id")
            .eq("story_id", project_id)
            .is_("embedding", "NULL")
            .execute()
        )
        need_embed = len(r_null.data or [])
        embedded = max(0, total_mem - need_embed)
    except Exception:
        total_mem = 0
        need_embed = 0
        embedded = 0

    st.caption(f"**Vector (Chat Memory / Crystallize):** {embedded} / {total_mem} entry có embedding.")
    _mem_sync_running = is_embedding_backfill_running("crystallize")
    if not _mem_sync_running and st.session_state.get("embedding_sync_clicked_crystallize"):
        st.session_state.pop("embedding_sync_clicked_crystallize", None)
    if _mem_sync_running or st.session_state.get("embedding_sync_clicked_crystallize", False):
        st.caption("⏳ Đang đồng bộ vector (Chat Memory). Vui lòng đợi xong rồi bấm Refresh.")
    col_vec1, col_vec2 = st.columns(2)
    with col_vec1:
        if st.button("🔄 Làm mới số liệu vector", key="chat_mem_refresh_vec", disabled=_mem_sync_running):
            st.rerun()
    with col_vec2:
        if st.button(
            "🔄 Đồng bộ vector (Chat Memory)",
            key="chat_mem_sync_vec_btn",
            disabled=(need_embed == 0 or _mem_sync_running),
        ):
            import threading

            st.session_state["embedding_sync_clicked_crystallize"] = True

            def _run():
                run_crystallize_embedding_backfill(project_id, limit=200)

            threading.Thread(target=_run, daemon=True).start()
            st.toast("Đã bắt đầu đồng bộ vector cho Chat Memory. Bấm **Làm mới số liệu vector** sau vài giây.")
            st.rerun()

    user = st.session_state.get("user")
    user_id = getattr(user, "id", None) if user else None
    user_email = getattr(user, "email", None) if user else None
    can_write = check_permission(str(user_id or ""), user_email or "", project_id, "write")
    can_delete = check_permission(str(user_id or ""), user_email or "", project_id, "delete")

    # Filter theo scope (giống Rules: Tất cả / project / arc)
    scope_filter = st.selectbox(
        "Phạm vi (scope)",
        ["Tất cả", "Chỉ project", "Chỉ arc"],
        index=0,
        key="chat_mem_scope_filter",
        help="Lọc điểm nhớ theo phạm vi áp dụng (project = toàn dự án, arc = theo arc).",
    )
    selected_arc_id = None
    if scope_filter == "Chỉ arc":
        try:
            from core.arc_service import ArcService
            arcs = ArcService.list_arcs(project_id, status="active") if project_id else []
        except Exception:
            arcs = []
        arc_labels = ["(Chọn arc)"] + [a.get("name", "") or a.get("id", "")[:8] for a in arcs]
        arc_ids = [None] + [a.get("id") for a in arcs]
        arc_idx = st.selectbox(
            "Arc",
            range(len(arc_labels)),
            index=0,
            format_func=lambda i: arc_labels[i] if i < len(arc_labels) else "",
            key="chat_mem_arc_filter",
        )
        selected_arc_id = arc_ids[arc_idx] if arc_idx < len(arc_ids) else None
        if not selected_arc_id:
            scope_filter = "Tất cả"

    # Danh sách từ chat_crystallize_entries (có scope, arc_id)
    page = max(1, int(st.session_state.get("chat_mem_page", 1)))
    try:
        cq = supabase.table("chat_crystallize_entries").select("id", count="exact").eq("story_id", project_id)
        if scope_filter == "Chỉ project":
            cq = cq.eq("scope", "project")
        elif scope_filter == "Chỉ arc" and selected_arc_id:
            cq = cq.eq("scope", "arc").eq("arc_id", selected_arc_id)
        count_res = cq.limit(0).execute()
        total_chat = getattr(count_res, "count", None) or 0
    except Exception:
        total_chat = 0
    total_pages = max(1, (total_chat + KNOWLEDGE_PAGE_SIZE - 1) // KNOWLEDGE_PAGE_SIZE)
    page = max(1, min(page, total_pages))
    st.session_state["chat_mem_page"] = page
    offset = (page - 1) * KNOWLEDGE_PAGE_SIZE
    try:
        r_q = (
            supabase.table("chat_crystallize_entries")
            .select("id, title, description, scope, arc_id, created_at")
            .eq("story_id", project_id)
            .order("created_at", desc=True)
        )
        if scope_filter == "Chỉ project":
            r_q = r_q.eq("scope", "project")
        elif scope_filter == "Chỉ arc" and selected_arc_id:
            r_q = r_q.eq("scope", "arc").eq("arc_id", selected_arc_id)
        r = r_q.range(offset, offset + KNOWLEDGE_PAGE_SIZE - 1).execute()
        chat_data = list(r.data or [])
    except Exception:
        chat_data = []
        total_chat = 0
        total_pages = 1

    st.metric("Tổng Chat Crystallize", total_chat)
    st.caption("Điểm nhớ từ hội thoại (Auto Crystallize). Sửa nội dung và **phạm vi (scope)** như Rules: project = toàn dự án, arc = chỉ áp dụng trong arc đã chọn.")

    if not chat_data and total_chat == 0:
        st.info("Chưa có điểm nhớ. Auto Crystallize sẽ tạo khi đủ 30 tin nhắn trong Chat.")
        return

    if total_pages > 1:
        pcol1, pcol2, pcol3 = st.columns([1, 2, 1])
        with pcol1:
            if st.button("⬅️ Trang trước", key="chat_mem_prev", disabled=(page <= 1)):
                st.session_state["chat_mem_page"] = max(1, page - 1)
                st.rerun()
        with pcol2:
            st.caption(f"**Trang {page} / {total_pages}** (tối đa {KNOWLEDGE_PAGE_SIZE} mục/trang)")
        with pcol3:
            if st.button("Trang sau ➡️", key="chat_mem_next", disabled=(page >= total_pages)):
                st.session_state["chat_mem_page"] = min(total_pages, page + 1)
                st.rerun()

    for entry in chat_data:
        label = entry.get("title", "") or ("Crystallize #%s" % entry.get("id", ""))
        scope_label = entry.get("scope") or "project"
        if entry.get("arc_id"):
            scope_label += " (arc)"
        with st.expander(f"**{label}** — scope: {scope_label}", expanded=False):
            st.markdown(entry.get("description", ""))
            col1, col2 = st.columns(2)
            with col1:
                if st.button("✏️ Sửa nội dung & scope", key=f"chat_edit_{entry['id']}") and can_write:
                    st.session_state["chat_editing"] = entry
            with col2:
                if can_delete and st.button("🗑️ Xóa", key=f"chat_del_{entry['id']}"):
                    try:
                        supabase.table("chat_crystallize_entries").delete().eq("id", entry["id"]).execute()
                        st.success("Đã xóa.")
                        invalidate_cache()
                        st.rerun()
                    except Exception as e:
                        st.error(str(e))

    if st.session_state.get("chat_editing") and can_write:
        e = st.session_state["chat_editing"]
        st.markdown("---")
        st.subheader("Sửa: %s" % (e.get("title", "") or "Crystallize"))
        with st.form("edit_chat_form"):
            new_title = st.text_input("Tiêu đề", value=e.get("title", ""))
            new_desc = st.text_area("Nội dung", value=e.get("description", ""), height=150)
            scope_options = ["project", "arc"]
            cur_scope = (e.get("scope") or "project").strip().lower()
            if cur_scope not in scope_options:
                cur_scope = "project"
            new_scope = st.selectbox(
                "Phạm vi (scope)",
                scope_options,
                index=scope_options.index(cur_scope),
                format_func=lambda x: "Project (toàn dự án)" if x == "project" else "Arc (chỉ arc đã chọn)",
                key="chat_edit_scope",
            )
            new_arc_id = None
            if new_scope == "arc":
                try:
                    from core.arc_service import ArcService
                    arcs = ArcService.list_arcs(project_id, status="active") if project_id else []
                except Exception:
                    arcs = []
                arc_labels = [a.get("name", "") or a.get("id", "")[:8] for a in arcs]
                arc_ids = [a.get("id") for a in arcs]
                cur_arc = e.get("arc_id")
                cur_idx = arc_ids.index(cur_arc) if cur_arc in arc_ids else 0
                arc_idx = st.selectbox("Arc", range(len(arc_labels)), index=min(cur_idx, len(arc_labels) - 1) if arc_labels else 0, format_func=lambda i: arc_labels[i] if i < len(arc_labels) else "", key="chat_edit_arc")
                if arc_labels and arc_ids:
                    new_arc_id = arc_ids[arc_idx] if arc_idx < len(arc_ids) else None
            if st.form_submit_button("💾 Cập nhật"):
                upd = {"title": (new_title or "").strip() or e.get("title"), "description": new_desc, "embedding": None, "scope": new_scope}
                if new_scope == "arc" and new_arc_id:
                    upd["arc_id"] = new_arc_id
                else:
                    upd["arc_id"] = None
                try:
                    supabase.table("chat_crystallize_entries").update(upd).eq("id", e["id"]).execute()
                    st.success("Đã cập nhật. Bấm **Đồng bộ vector (Chat Memory)** trên nếu cần cập nhật embedding.")
                    st.session_state["update_trigger"] = st.session_state.get("update_trigger", 0) + 1
                    del st.session_state["chat_editing"]
                    invalidate_cache()
                    st.rerun()
                except Exception as ex:
                    st.error(str(ex))
            if st.form_submit_button("Hủy"):
                del st.session_state["chat_editing"]
                st.rerun()
