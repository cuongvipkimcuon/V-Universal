# views/collaboration.py - Tab Cộng tác: Members + Pending Requests (cho Owner)
import json
import streamlit as st

from config import init_services
from utils.auth_manager import (
    get_user_role,
    check_permission,
    get_pending_changes,
    approve_pending_change,
    reject_pending_change,
)


def render_collaboration_tab(project_id):
    """Tab Collaboration: Members (add/set role) và Pending Requests (diff + Approve/Reject)."""
    st.subheader("👥 Collaboration")

    if not project_id:
        st.info("📁 Chọn Project ở thanh bên trái.")
        return

    user_id = getattr(st.session_state.get("user"), "id", None) or ""
    user_email = getattr(st.session_state.get("user"), "email", None) or ""
    role = get_user_role(user_id, user_email, project_id)

    if role != "owner":
        st.info("Chỉ Owner của project mới có thể quản lý thành viên và duyệt yêu cầu chỉnh sửa.")
        return

    tab_members, tab_pending = st.tabs(["Members", "Pending Requests"])

    with tab_members:
        _render_members_tab(project_id)

    with tab_pending:
        _render_pending_tab(project_id)


def _render_members_tab(project_id):
    """Tab Members: danh sách thành viên + form thêm email và set role."""
    st.markdown("#### Thành viên")
    services = init_services()
    if not services:
        st.error("Không kết nối được dịch vụ.")
        return
    supabase = services["supabase"]

    try:
        members = (
            supabase.table("project_members")
            .select("*")
            .eq("story_id", project_id)
            .execute()
        )
        rows = members.data if members.data else []
    except Exception as e:
        st.warning(f"Bảng project_members có thể chưa tồn tại: {e}")
        rows = []

    # Owner thật (từ stories) không nằm trong project_members; hiển thị riêng
    try:
        story = (
            supabase.table("stories")
            .select("user_id")
            .eq("id", project_id)
            .execute()
        )
        owner_id = story.data[0].get("user_id") if story.data else None
    except Exception:
        owner_id = None

    if owner_id:
        st.caption("Owner (bạn) — Full quyền. Thành viên bên dưới do bạn mời.")

    for r in rows:
        email = r.get("user_email") or ""
        rl = (r.get("role") or "viewer").lower()
        st.markdown(f"- **{email}** — {rl}")

    st.markdown("---")
    st.markdown("**Thêm thành viên**")
    with st.form("add_member_form"):
        new_email = st.text_input("Email thành viên", placeholder="user@example.com")
        new_role = st.selectbox(
            "Vai trò",
            ["partner", "viewer"],
            format_func=lambda x: "Partner (đọc + gửi yêu cầu sửa)" if x == "partner" else "Viewer (chỉ đọc)",
        )
        if st.form_submit_button("Thêm"):
            if new_email and new_email.strip():
                try:
                    supabase.table("project_members").insert({
                        "story_id": project_id,
                        "user_email": new_email.strip().lower(),
                        "role": new_role,
                    }).execute()
                    st.success(f"Đã thêm {new_email} với vai trò {new_role}.")
                except Exception as ex:
                    st.error(f"Lỗi: {ex}")
            else:
                st.warning("Nhập email.")

    # Nút xóa thành viên (tùy chọn)
    if rows:
        st.markdown("---")
        to_remove = st.selectbox(
            "Gỡ thành viên",
            [""] + [f"{r.get('user_email')} ({r.get('role')})" for r in rows],
            key="remove_member_select",
        )
        if to_remove and st.button("Gỡ khỏi project"):
            email = to_remove.split(" (")[0].strip()
            try:
                supabase.table("project_members").delete().eq(
                    "story_id", project_id
                ).eq("user_email", email).execute()
                st.success("Đã gỡ thành viên.")
            except Exception as ex:
                st.error(f"Lỗi: {ex}")


def _render_pending_tab(project_id):
    """Tab Pending Requests: load pending_changes, diff view, Approve / Reject."""
    st.markdown("#### Yêu cầu chỉnh sửa đang chờ")
    pending = get_pending_changes(project_id, status="pending")
    if not pending:
        st.info("Chưa có yêu cầu nào.")
        return

    for rec in pending:
        req_id = rec.get("id")
        by_email = rec.get("requested_by_email") or ""
        table_name = rec.get("table_name") or ""
        target_key = rec.get("target_key") or {}
        old_data = rec.get("old_data") or {}
        new_data = rec.get("new_data") or {}

        with st.expander(f"📝 {table_name} — bởi {by_email}", expanded=True):
            st.caption(f"Target: {json.dumps(target_key, ensure_ascii=False)}")
            col_old, col_new = st.columns(2)
            with col_old:
                st.markdown("**Nội dung cũ**")
                st.json(old_data)
            with col_new:
                st.markdown("**Nội dung mới**")
                st.json(new_data)
            col_approve, col_reject, _ = st.columns([1, 1, 2])
            with col_approve:
                if st.button("✅ Approve", key=f"approve_{req_id}"):
                    if approve_pending_change(str(req_id)):
                        st.success("Đã duyệt và áp dụng thay đổi.")
                    else:
                        st.error("Không thể áp dụng.")
            with col_reject:
                if st.button("❌ Reject", key=f"reject_{req_id}"):
                    if reject_pending_change(str(req_id)):
                        st.success("Đã từ chối.")
                    else:
                        st.error("Lỗi từ chối.")
