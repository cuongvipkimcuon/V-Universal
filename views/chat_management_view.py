# views/chat_management_view.py - Quản lý Bible entries [CHAT] (Auto Crystallize)
"""Tab quản lý [CHAT]: xem, sửa nội dung, xóa, Archive/Unarchive. Không add tay - chỉ Auto Crystallize tạo."""
import streamlit as st

from config import init_services
from ai_engine import AIService
from utils.auth_manager import check_permission
from utils.cache_helpers import get_bible_list_cached, invalidate_cache


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
    bible_data_all = get_bible_list_cached(project_id, st.session_state.get("update_trigger", 0))
    chat_data = [e for e in bible_data_all if (e.get("entity_name") or "").startswith("[CHAT]")]
    user = st.session_state.get("user")
    user_id = getattr(user, "id", None) if user else None
    user_email = getattr(user, "email", None) if user else None
    can_write = check_permission(str(user_id or ""), user_email or "", project_id, "write")
    can_delete = check_permission(str(user_id or ""), user_email or "", project_id, "delete")

    archived_count = sum(1 for e in chat_data if e.get("archived"))
    st.metric("Tổng [CHAT] entries", len(chat_data))
    if archived_count:
        st.caption("📦 Đã archive: %s (không đưa vào context, chỉ hiện nút Unarchive)." % archived_count)

    if not chat_data:
        st.info("Chưa có điểm nhớ [CHAT]. Auto Crystallize sẽ tạo khi đủ 30 tin nhắn trong Chat.")
        return

    for entry in chat_data:
        is_archived = entry.get("archived") is True
        label = "%s %s" % ("📦", entry.get("entity_name", "")) if is_archived else entry.get("entity_name", "")
        with st.expander(f"**{label}**", expanded=False):
            st.markdown(entry.get("description", ""))
            if is_archived:
                if st.button("📤 Unarchive", key=f"chat_unarchive_{entry['id']}", type="primary") and can_write:
                    try:
                        supabase.table("story_bible").update({"archived": False}).eq("id", entry["id"]).execute()
                        st.success("Đã bỏ archive.")
                        invalidate_cache()
                    except Exception as e:
                        st.error(str(e))
            else:
                col1, col2, col3 = st.columns(3)
                with col1:
                    if st.button("✏️ Sửa nội dung", key=f"chat_edit_{entry['id']}") and can_write:
                        st.session_state["chat_editing"] = entry
                with col2:
                    if can_delete and st.button("🗑️ Xóa", key=f"chat_del_{entry['id']}"):
                        try:
                            supabase.table("story_bible").delete().eq("id", entry["id"]).execute()
                            st.success("Đã xóa.")
                            invalidate_cache()
                        except Exception as e:
                            st.error(str(e))
                with col3:
                    if st.button("📦 Archive", key=f"chat_archive_{entry['id']}") and can_write:
                        try:
                            supabase.table("story_bible").update({"archived": True}).eq("id", entry["id"]).execute()
                            st.success("Đã archive (sẽ không đưa vào context).")
                            invalidate_cache()
                        except Exception as e:
                            st.error(str(e))

    if st.session_state.get("chat_editing") and can_write:
        e = st.session_state["chat_editing"]
        st.markdown("---")
        st.subheader(f"Sửa: {e.get('entity_name', '')}")
        st.caption("Chỉ sửa nội dung (description). Tiền tố [CHAT] không thay đổi.")
        with st.form("edit_chat_form"):
            new_desc = st.text_area("Nội dung", value=e.get("description", ""), height=150)
            if st.form_submit_button("💾 Cập nhật"):
                upd = {"description": new_desc, "embedding": None}
                try:
                    supabase.table("story_bible").update(upd).eq("id", e["id"]).execute()
                    st.success("Đã cập nhật. Bấm **Đồng bộ vector (Bible)** trong tab Bible nếu cần cập nhật embedding.")
                    st.session_state["update_trigger"] = st.session_state.get("update_trigger", 0) + 1
                    del st.session_state["chat_editing"]
                    invalidate_cache()
                except Exception as ex:
                    st.error(str(ex))
            if st.form_submit_button("Hủy"):
                del st.session_state["chat_editing"]
