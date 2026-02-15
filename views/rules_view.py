# views/rules_view.py - Vùng Rules (chỉ [RULE] từ Bible)
"""Hiển thị và quản lý Rules. Thêm/sửa: tự tạo vector. Danger Zone xóa sạch."""
import re
import streamlit as st

from config import Config, init_services
from ai_engine import AIService
from utils.auth_manager import check_permission
from utils.cache_helpers import get_bible_list_cached, invalidate_cache, full_refresh


def render_rules_tab(project_id, persona):
    st.header("📋 Rules")
    st.caption("Ver 7.0: Quy tắc dự án. Thêm/sửa tự tạo vector như Bible.")

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
    rules_data = [e for e in bible_data_all if "[RULE]" in (e.get("entity_name") or "")]
    user = st.session_state.get("user")
    user_id = getattr(user, "id", None) if user else None
    user_email = getattr(user, "email", None) if user else None
    can_write = check_permission(str(user_id or ""), user_email or "", project_id, "write")
    can_delete = check_permission(str(user_id or ""), user_email or "", project_id, "delete")

    st.metric("Tổng Rules", len(rules_data))

    if st.button("➕ Thêm Rule mới", key="rules_add") and can_write:
        st.session_state["rules_adding"] = True

    if st.session_state.get("rules_adding") and can_write:
        st.markdown("---")
        with st.form("add_rule_form"):
            rule_content = st.text_area("Nội dung Rule", height=100, key="new_rule_content")
            if st.form_submit_button("💾 Lưu"):
                if rule_content and rule_content.strip():
                    vec = AIService.get_embedding(rule_content)
                    try:
                        payload = {
                            "story_id": project_id,
                            "entity_name": f"[RULE] {(rule_content[:47] + '...') if len(rule_content) > 50 else rule_content}",
                            "description": rule_content.strip(),
                            "source_chapter": 0,
                        }
                        if vec:
                            payload["embedding"] = vec
                        supabase.table("story_bible").insert(payload).execute()
                        st.success("Đã thêm Rule (vector tự tạo).")
                        st.session_state["update_trigger"] = st.session_state.get("update_trigger", 0) + 1
                        st.session_state["rules_adding"] = False
                        invalidate_cache()
                    except Exception as e:
                        payload.pop("embedding", None)
                        supabase.table("story_bible").insert(payload).execute()
                        st.success("Đã thêm.")
                        st.session_state["rules_adding"] = False
                        invalidate_cache()
            if st.form_submit_button("Hủy"):
                st.session_state["rules_adding"] = False

    st.markdown("---")
    if not rules_data:
        st.info("Chưa có Rule nào.")
        return

    for entry in rules_data:
        with st.expander(f"**{entry.get('entity_name','')[:60]}**", expanded=False):
            st.markdown(entry.get("description", ""))
            col1, col2 = st.columns(2)
            with col1:
                if st.button("✏️ Sửa", key=f"rule_edit_{entry['id']}") and can_write:
                    st.session_state["rules_editing"] = entry
            with col2:
                if can_delete and st.button("🗑️ Xóa", key=f"rule_del_{entry['id']}"):
                    try:
                        supabase.table("story_bible").delete().eq("id", entry["id"]).execute()
                        st.success("Đã xóa.")
                        invalidate_cache()
                    except Exception as e:
                        st.error(str(e))

    if st.session_state.get("rules_editing") and can_write:
        e = st.session_state["rules_editing"]
        st.markdown("---")
        with st.form("edit_rule_form"):
            new_desc = st.text_area("Nội dung", value=e.get("description", ""), height=100)
            if st.form_submit_button("💾 Cập nhật (tự tạo vector mới)"):
                vec = AIService.get_embedding(new_desc)
                upd = {"description": new_desc}
                if vec:
                    upd["embedding"] = vec
                try:
                    supabase.table("story_bible").update(upd).eq("id", e["id"]).execute()
                except Exception:
                    upd.pop("embedding", None)
                    supabase.table("story_bible").update(upd).eq("id", e["id"]).execute()
                st.success("Đã cập nhật.")
                del st.session_state["rules_editing"]
                invalidate_cache()
            if st.form_submit_button("Hủy"):
                del st.session_state["rules_editing"]

    st.markdown("---")
    with st.expander("💀 Danger Zone", expanded=False):
        st.markdown('<div class="danger-zone">', unsafe_allow_html=True)
        if can_delete:
            confirm = st.checkbox("Xóa sạch TẤT CẢ Rules", key="rules_confirm_clear")
            if confirm and st.button("🗑️ Xóa sạch Rules"):
                ids = [r["id"] for r in rules_data]
                if ids:
                    supabase.table("story_bible").delete().in_("id", ids).execute()
                    st.success("Đã xóa sạch Rules.")
                    invalidate_cache()
        st.markdown("</div>", unsafe_allow_html=True)
