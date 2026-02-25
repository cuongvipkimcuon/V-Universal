# views/semantic_intent_view.py - Semantic Intent: mẫu câu hỏi + data (context + câu trả lời)
"""UI Semantic Intent: data = context + câu trả lời. Ngưỡng 85-100%. Embed theo câu hỏi (question_sample)."""
import streamlit as st
from datetime import datetime

from config import init_services
from ai_engine import AIService
from utils.auth_manager import check_permission
from core.background_jobs import run_semantic_intent_embedding_backfill, is_embedding_backfill_running


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

    # Tùy chọn Semantic Intent: dạng khẳng định (bật = dùng). Mặc định: KHÔNG auto-create, KHÔNG dùng khi trả lời.
    st.caption("**Tùy chọn Semantic Intent** — bật = dùng trong V Work chat. Mặc định tắt (an toàn).")
    # Ở tầng settings vẫn lưu dạng phủ định semantic_intent_no_auto_create / semantic_intent_no_use cho tương thích.
    opt_no_auto = _get_setting(supabase, "semantic_intent_no_auto_create", True)
    opt_no_use = _get_setting(supabase, "semantic_intent_no_use", True)
    col_opt1, col_opt2 = st.columns(2)
    with col_opt1:
        use_auto = st.toggle(
            "Tự động lưu Semantic từ V Work",
            value=not opt_no_auto,
            key="si_use_auto",
            help="Bật = Sau mỗi câu trả lời phù hợp, V Work có thể lưu một mẫu Semantic Intent (ở trạng thái chưa duyệt).",
        )
    with col_opt2:
        use_in_chat = st.toggle(
            "Dùng Semantic Intent khi trả lời",
            value=not opt_no_use,
            key="si_use_in_chat",
            help="Bật = Khi câu hỏi khớp mạnh với một Semantic Intent đã duyệt, V Work dùng data đó làm context chính để trả lời nhanh.",
        )
    if st.button("💾 Lưu tùy chọn", key="si_save_opts"):
        try:
            # Chuyển ngược về dạng phủ định khi lưu (no_auto/no_use) để giữ tương thích với phần còn lại của code.
            no_auto = not use_auto
            no_use = not use_in_chat
            for k, v in [
                ("semantic_intent_no_auto_create", 1 if no_auto else 0),
                ("semantic_intent_no_use", 1 if no_use else 0),
            ]:
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

    # Số mẫu đã embed (theo câu hỏi) vs tổng — giống Bible
    try:
        r_all = (
            supabase.table("semantic_intent")
            .select("id")
            .eq("story_id", project_id)
            .execute()
        )
        total_count = len(r_all.data or [])
        r_null = (
            supabase.table("semantic_intent")
            .select("id")
            .eq("story_id", project_id)
            .is_("embedding", "NULL")
            .execute()
        )
        no_embed_count = len(r_null.data or [])
        embed_count = max(0, total_count - no_embed_count)
    except Exception:
        total_count = 0
        no_embed_count = 0
        embed_count = 0
    st.caption(f"**Vector (embed theo câu hỏi):** {embed_count} / {total_count} mẫu đã có embedding.")
    _semantic_sync_running = is_embedding_backfill_running("semantic_intent")
    if not _semantic_sync_running:
        st.session_state.pop("embedding_sync_clicked_semantic", None)
    if _semantic_sync_running:
        st.caption("⏳ Đang đồng bộ vector (Semantic Intent). Vui lòng đợi xong rồi bấm Refresh.")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("🔄 Làm mới số liệu", key="si_refresh_vec", disabled=_semantic_sync_running):
            st.rerun()
    with c2:
        if st.button("🔄 Đồng bộ vector (Semantic Intent)", key="si_sync_vec_btn", disabled=(no_embed_count == 0 or _semantic_sync_running)):
            import threading
            st.session_state["embedding_sync_clicked_semantic"] = True

            def _run():
                run_semantic_intent_embedding_backfill(project_id, limit=200)

            threading.Thread(target=_run, daemon=True).start()
            st.toast("Đã bắt đầu đồng bộ vector (embed theo câu hỏi). Bấm **Làm mới số liệu** sau vài giây.")
            st.rerun()

    st.markdown("---")

    # List (cả đã duyệt và chưa duyệt)
    r = (
        supabase.table("semantic_intent")
        .select("*")
        .eq("story_id", project_id)
        .order("created_at", desc=True)
        .execute()
    )
    items = r.data or []

    st.metric("Tổng mẫu", len(items))

    # Filter trạng thái duyệt cho Semantic Intent
    status_filter = st.selectbox(
        "Trạng thái",
        ["Tất cả", "Chỉ đã duyệt", "Chỉ chưa duyệt"],
        index=0,
        key="si_status_filter",
        help="Lọc Semantic Intent theo trạng thái duyệt. Chỉ mẫu đã duyệt mới được dùng để match.",
    )
    if status_filter == "Chỉ đã duyệt":
        items = [x for x in items if bool(x.get("approve", True))]
    elif status_filter == "Chỉ chưa duyệt":
        items = [x for x in items if not bool(x.get("approve", True))]

    if st.button("➕ Thêm mẫu", key="si_add") and can_write:
        st.session_state["si_adding"] = True

    if st.session_state.get("si_adding") and can_write:
        with st.form("si_add_form"):
            q = st.text_area("Mẫu câu hỏi", placeholder="VD: Tổng doanh thu tháng này?")
            data = st.text_area("Data (context + câu trả lời)", placeholder="Ôm hết context tạo ra nó + câu trả lời. Nhập tay hoặc lưu từ chat.", height=200)
            if st.form_submit_button("💾 Lưu"):
                if q and q.strip():
                    payload = {
                        "story_id": project_id,
                        "question_sample": q.strip(),
                        "intent": "chat_casual",
                        "related_data": data or "",
                        "approve": True,
                    }
                    try:
                        supabase.table("semantic_intent").insert(payload).execute()
                        st.success("Đã thêm (embedding chỉ tạo khi có nút đồng bộ vector cho mục này).")
                        st.session_state["si_adding"] = False
                    except Exception as e:
                        st.error(str(e))
            if st.form_submit_button("Hủy"):
                st.session_state["si_adding"] = False

    st.markdown("---")
    for item in items:
        has_emb = bool(item.get("embedding"))
        sync_badge = "" if has_emb else " 🔄 Chưa embed"
        approved = bool(item.get("approve", True))
        approve_badge = " ✅ ĐÃ DUYỆT" if approved else " ⏳ CHƯA DUYỆT"
        with st.expander(f"**{item.get('question_sample','')[:60]}**{sync_badge}{approve_badge}", expanded=False):
            if not has_emb:
                st.caption("🔄 Chưa đồng bộ vector — bấm **Đồng bộ vector (Semantic Intent)** trên để embed theo câu hỏi.")
            st.write("**Data:**", (item.get("related_data") or "")[:500])
            col1, col2, col3 = st.columns(3)
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
            with col3:
                if can_write:
                    if not approved and st.button("✅ Approve", key=f"si_approve_{item.get('id')}"):
                        try:
                            supabase.table("semantic_intent").update({"approve": True, "updated_at": datetime.utcnow().isoformat()}).eq("id", item["id"]).execute()
                            st.success("Đã duyệt Semantic Intent.")
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
                    upd = {
                        "question_sample": q.strip(),
                        "intent": "chat_casual",
                        "related_data": data or "",
                        "updated_at": datetime.utcnow().isoformat(),
                        "embedding": None,  # sửa câu hỏi/data → xóa embedding để đồng bộ lại theo câu hỏi
                    }
                    try:
                        supabase.table("semantic_intent").update(upd).eq("id", edit_id).execute()
                        del st.session_state["si_editing"]
                        st.success("Đã cập nhật. Bấm **Đồng bộ vector (Semantic Intent)** để embed lại theo câu hỏi.")
                    except Exception as e:
                        st.error(str(e))
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
