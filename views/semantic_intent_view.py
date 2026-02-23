# views/semantic_intent_view.py - Semantic Intent: mẫu câu hỏi + data (context + câu trả lời)
"""UI Semantic Intent: data = context + câu trả lời. Ngưỡng 85-100%. Không cần intent."""
import streamlit as st
from datetime import datetime

from config import init_services
from ai_engine import AIService
from utils.auth_manager import check_permission


def _ensure_table(supabase):
    try:
        supabase.table("semantic_intent").select("id").limit(1).execute()
        return True
    except Exception:
        return False


def _get_threshold(supabase):
    try:
        r = supabase.table("settings").select("value").eq("key", "semantic_intent_threshold").execute()
        if r.data and r.data[0]:
            return int(r.data[0].get("value", 90))
    except Exception:
        pass
    return 90


def _get_setting(supabase, key: str, default: bool) -> bool:
    try:
        r = supabase.table("settings").select("value").eq("key", key).execute()
        if r.data and r.data[0]:
            v = r.data[0].get("value")
            if v is not None:
                return bool(int(v))
    except Exception:
        pass
    return default


def render_semantic_intent_tab(project_id):
    st.subheader("🎯 Semantic Intent")
    st.caption("Mẫu câu hỏi + data (context + câu trả lời). Vector hóa. Khớp ≥ ngưỡng thì dùng data trực tiếp.")

    if not project_id:
        st.info("📁 Chọn Project trước.")
        return

    services = init_services()
    if not services:
        st.warning("Không kết nối được dịch vụ.")
        return
    supabase = services["supabase"]

    if not _ensure_table(supabase):
        st.warning("Bảng semantic_intent chưa tồn tại. Chạy schema_v6.6_migration.sql.")
        return

    user = st.session_state.get("user")
    user_id = getattr(user, "id", None) if user else None
    user_email = getattr(user, "email", None) if user else None
    can_write = check_permission(str(user_id or ""), user_email or "", project_id, "write")
    can_delete = check_permission(str(user_id or ""), user_email or "", project_id, "delete")

    # Tùy chọn (dạng phủ định: bật = tắt hành vi tương ứng trong V Work chat)
    st.caption("**Tùy chọn phủ định** — bật = tắt hành vi trong V Work chat. Lưu bên dưới có hiệu lực toàn project.")
    opt_no_auto = _get_setting(supabase, "semantic_intent_no_auto_create", False)
    opt_no_use = _get_setting(supabase, "semantic_intent_no_use", False)
    col_opt1, col_opt2 = st.columns(2)
    with col_opt1:
        no_auto = st.toggle("Không gợi ý thêm mẫu Semantic sau mỗi câu trả lời", value=opt_no_auto, key="si_no_auto",
                            help="Bật = V Work chat không hiện 'Thêm vào Semantic Intent?' sau reply.")
    with col_opt2:
        no_use = st.toggle("Không dùng semantic intent để tạo câu trả lời", value=opt_no_use, key="si_no_use",
                           help="Bật = V Work bỏ qua semantic intent, luôn dùng Router.")
    if st.button("💾 Lưu tùy chọn", key="si_save_opts"):
        try:
            for k, v in [("semantic_intent_no_auto_create", 1 if no_auto else 0), ("semantic_intent_no_use", 1 if no_use else 0)]:
                try:
                    supabase.table("settings").upsert({"key": k, "value": v}, on_conflict="key").execute()
                except Exception:
                    supabase.table("settings").insert({"key": k, "value": v}).execute()
            st.toast("Đã lưu.")
        except Exception as e:
            st.error(str(e))

    # Ngưỡng
    threshold = st.slider("Ngưỡng khớp (%)", 85, 100, _get_threshold(supabase), 1,
                          help="85-100%. Thấp hơn dễ sai. Mặc định 90%.")
    if can_write and st.button("💾 Lưu ngưỡng"):
        try:
            try:
                supabase.table("settings").upsert({"key": "semantic_intent_threshold", "value": threshold}, on_conflict="key").execute()
            except Exception:
                supabase.table("settings").insert({"key": "semantic_intent_threshold", "value": threshold}).execute()
            st.toast("Đã lưu ngưỡng.")
        except Exception as e:
            st.error(str(e))

    st.markdown("---")

    # List
    r = supabase.table("semantic_intent").select("*").eq("story_id", project_id).order("created_at", desc=True).execute()
    items = r.data or []

    st.metric("Tổng mẫu", len(items))

    if st.button("➕ Thêm mẫu", key="si_add") and can_write:
        st.session_state["si_adding"] = True

    if st.session_state.get("si_adding") and can_write:
        with st.form("si_add_form"):
            q = st.text_area("Mẫu câu hỏi", placeholder="VD: Tổng doanh thu tháng này?")
            data = st.text_area("Data (context + câu trả lời)", placeholder="Ôm hết context tạo ra nó + câu trả lời. Nhập tay hoặc lưu từ chat.", height=200)
            if st.form_submit_button("💾 Lưu"):
                if q and q.strip():
                    vec = AIService.get_embedding(q.strip())
                    payload = {"story_id": project_id, "question_sample": q.strip(), "intent": "chat_casual", "related_data": data or ""}
                    if vec:
                        payload["embedding"] = vec
                    try:
                        supabase.table("semantic_intent").insert(payload).execute()
                        st.success("Đã thêm.")
                        st.session_state["si_adding"] = False
                    except Exception as e:
                        payload.pop("embedding", None)
                        supabase.table("semantic_intent").insert(payload).execute()
                        st.success("Đã thêm (chưa vector).")
                        st.session_state["si_adding"] = False
            if st.form_submit_button("Hủy"):
                st.session_state["si_adding"] = False

    st.markdown("---")
    for item in items:
        with st.expander(f"**{item.get('question_sample','')[:60]}**", expanded=False):
            st.write("**Data:**", (item.get("related_data") or "")[:500])
            col1, col2 = st.columns(2)
            with col1:
                if st.button("✏️ Sửa", key=f"si_edit_{item.get('id')}"):
                    st.session_state["si_editing"] = item.get("id")
            with col2:
                if can_delete and st.button("🗑️ Xóa", key=f"si_del_{item.get('id')}"):
                    try:
                        supabase.table("semantic_intent").delete().eq("id", item["id"]).execute()
                        st.success("Đã xóa.")
                    except Exception as e:
                        st.error(str(e))

    if st.session_state.get("si_editing") and can_write:
        edit_id = st.session_state["si_editing"]
        row = next((x for x in items if str(x.get("id")) == str(edit_id)), None)
        if row:
            st.markdown("---")
            with st.form("si_edit_form"):
                q = st.text_area("Mẫu câu hỏi", value=row.get("question_sample", ""))
                data = st.text_area("Data (context + câu trả lời)", value=row.get("related_data", ""), height=200)
                if st.form_submit_button("💾 Cập nhật"):
                    vec = AIService.get_embedding(q.strip()) if q.strip() else None
                    upd = {"question_sample": q.strip(), "intent": "chat_casual", "related_data": data or "", "updated_at": datetime.utcnow().isoformat()}
                    if vec:
                        upd["embedding"] = vec
                    try:
                        supabase.table("semantic_intent").update(upd).eq("id", edit_id).execute()
                        del st.session_state["si_editing"]
                        st.success("Đã cập nhật.")
                    except Exception as e:
                        upd.pop("embedding", None)
                        supabase.table("semantic_intent").update(upd).eq("id", edit_id).execute()
                        del st.session_state["si_editing"]
                if st.form_submit_button("Hủy"):
                    del st.session_state["si_editing"]

    # Danger Zone
    st.markdown("---")
    with st.expander("💀 Danger Zone", expanded=False):
        st.markdown('<div class="danger-zone">', unsafe_allow_html=True)
        if can_delete:
            confirm = st.checkbox("Tôi chắc chắn muốn xóa TẤT CẢ semantic intent", key="si_confirm_clear")
            if confirm and st.button("🗑️ Xóa sạch Semantic Intent", type="primary"):
                try:
                    supabase.table("semantic_intent").delete().eq("story_id", project_id).execute()
                    st.success("Đã xóa sạch.")
                except Exception as e:
                    st.error(str(e))
        st.markdown("</div>", unsafe_allow_html=True)
