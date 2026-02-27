import streamlit as st

from config import Config, init_services
from .setup_tabs import render_prefix_setup, render_persona_setup


def render_settings_tab():
    """Tab Settings Ver 7.0 — Account, AI Model (từ sidebar), Cấu hình AI, Giao diện, Bible & Personas."""
    st.header("⚙️ Settings")
    st.caption("Ver 7.0: Tất cả tùy chỉnh AI chuyển vào đây.")

    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
        "👤 Account",
        "🤖 AI Model",
        "⚙️ Cấu hình AI",
        "🎨 Giao diện",
        "📋 Bible & Personas",
        "🚀 V8 & Observability",
    ])

    with tab1:
        st.subheader("👤 Account Settings")
        if "user" in st.session_state:
            user_email = st.session_state.user.email
            st.info(f"Đăng nhập: **{user_email}**")
        with st.form("change_password"):
            current_pass = st.text_input("Mật khẩu hiện tại", type="password", help="Nhập mật khẩu để đổi.")
            new_pass = st.text_input("Mật khẩu mới", type="password")
            confirm_pass = st.text_input("Xác nhận mật khẩu mới", type="password")
            if st.form_submit_button("🔐 Đổi mật khẩu", type="primary"):
                if new_pass == confirm_pass:
                    st.success("Chức năng đổi mật khẩu sẽ tích hợp với Supabase Auth.")
                else:
                    st.error("Hai mật khẩu mới không khớp.")

    with tab2:
        st.subheader("🤖 AI Model (Model, Temperature, Context)")
        model_category = st.selectbox("Model Category", list(Config.AVAILABLE_MODELS.keys()), key="settings_model_cat")
        available = Config.AVAILABLE_MODELS[model_category]
        selected = st.selectbox("Model", available, key="settings_model")
        st.session_state["selected_model"] = selected
        st.session_state["temperature"] = st.slider("Temperature", 0.0, 1.0, 0.7, 0.1, key="settings_temp")
        st.session_state["context_size"] = st.select_slider("Context Size", ["low", "medium", "high", "max"], "medium", key="settings_ctx")
        st.caption("Model trên dùng cho Chat và Workstation.")
        all_models_flat = [m for models in Config.AVAILABLE_MODELS.values() for m in models]
        default_tool = st.session_state.get("default_ai_model", getattr(Config, "DEFAULT_TOOL_MODEL", Config.ROUTER_MODEL))
        if default_tool not in all_models_flat:
            default_tool = getattr(Config, "DEFAULT_TOOL_MODEL", Config.ROUTER_MODEL)
        default_idx = all_models_flat.index(default_tool) if default_tool in all_models_flat else 0
        st.session_state["default_ai_model"] = st.selectbox(
            "Model mặc định (công cụ)",
            all_models_flat,
            index=default_idx,
            key="settings_default_tool_model",
            help="Dùng cho Numerical Executor (V7), Data Analyze (trích xuất), Python Executor. Mặc định DeepSeek.",
        )
        st.success("Đã áp dụng. Chat/Workstation dùng Model trên; công cụ dùng Model mặc định.")

    with tab3:
        st.subheader("⚙️ Cấu hình AI chi tiết")
        st.selectbox(
            "Nhóm model mặc định",
            list(Config.AVAILABLE_MODELS.keys()),
            index=1,
            key="default_category",
            help="Nhóm model hiển thị mặc định trên sidebar.",
        )
        st.multiselect(
            "Loại trừ model",
            [m for models in Config.AVAILABLE_MODELS.values() for m in models],
            key="model_blacklist",
            help="Các model không hiển thị trong danh sách chọn.",
        )
        col_b1, col_b2 = st.columns(2)
        with col_b1:
            st.checkbox("Tự chuyển model rẻ khi hết credits", value=True, key="auto_switch")
            st.checkbox("Bật rule mining từ chat", value=True, key="enable_rule_mining")
        with col_b2:
            st.checkbox("Ưu tiên model nhanh cho câu ngắn", value=True, key="prefer_fast")
            st.checkbox("Luôn đưa luật bắt buộc vào context", value=True, key="include_rules")
        custom_prefixes = st.text_area(
            "Tiền tố Bible (dòng text, fallback)",
            value="\n".join(Config.get_prefixes()),
            height=120,
            help="Một dòng một prefix dạng [PREFIX]. Dùng khi chưa cấu hình bảng Bible Prefix bên dưới.",
        )
        if st.button("💾 Lưu cấu hình AI", type="primary"):
            if custom_prefixes:
                prefixes = [p.strip() for p in custom_prefixes.split("\n") if p.strip()]
                for k in getattr(Config, "PREFIX_SPECIAL_SYSTEM", ()) or ():
                    if k != "OTHER" and f"[{k}]" not in prefixes:
                        prefixes.append(f"[{k}]")
                try:
                    services = init_services()
                    if services:
                        services["supabase"].table("settings").upsert(
                            {"key": "bible_prefixes", "value": list(set(prefixes))},
                            on_conflict="key",
                        ).execute()
                    st.success("Đã lưu.")
                except Exception as e:
                    st.error(f"Lỗi: {e}")
            else:
                st.warning("Nhập ít nhất một prefix.")

    with tab4:
        st.subheader("🎨 Giao diện")
        theme = st.selectbox("Theme", ["Light", "Dark", "Auto"], index=2, help="Giao diện sáng/tối/tự động.")
        font_size = st.select_slider("Cỡ chữ", options=["Small", "Medium", "Large"], value="Medium")
        chat_density = st.select_slider("Mật độ Chat", options=["Compact", "Comfortable", "Spacious"], value="Comfortable")
        if st.button("✅ Áp dụng giao diện", type="primary"):
            st.success("Đã áp dụng (có thể cần refresh trang).")

    with tab5:
        st.caption("Quản lý Tiền tố Bible (gắn persona) và Personas. RULE, CHAT, OTHER không gắn persona.")
        with st.expander("📋 Quản lý Tiền tố Bible", expanded=True):
            render_prefix_setup()
        with st.expander("🎭 Cấu hình Personas", expanded=False):
            render_persona_setup()

    with tab6:
        st.subheader("🚀 V8 & Observability")
        st.caption("V8.3: Search context luôn gather đủ Bible, chunk, relation, timeline, chapter (+ bổ sung toàn văn chương khi context thiếu).")
        try:
            services = init_services()
            supabase = services.get("supabase") if services else None
        except Exception:
            supabase = None
        if supabase:
            try:
                r = supabase.table("settings").select("value").eq("key", "v8_full_context_search").execute()
                current = (r.data and r.data[0] and str(r.data[0].get("value") or "") == "1")
            except Exception:
                current = True
            v8_full = st.toggle(
                "V8 Full context search (search_context)",
                value=current,
                key="v8_full_context_toggle",
                help="Bật: intent search_context luôn lấy đủ bible, relation, timeline, chunk, chapter. Tắt: dùng lại context_needs từ Router (có thể thiếu ý).",
            )
            if st.button("💾 Lưu V8 Full context", key="save_v8_full"):
                try:
                    val = "1" if v8_full else "0"
                    supabase.table("settings").upsert({"key": "v8_full_context_search", "value": val}, on_conflict="key").execute()
                    st.toast("Đã lưu.")
                except Exception as e:
                    st.error(str(e))
            st.divider()
            try:
                r_ar = supabase.table("settings").select("value").eq("key", "enable_auto_reverse_full_chapter").execute()
                current_ar = (r_ar.data and r_ar.data[0] and str(r_ar.data[0].get("value") or "") == "1")
            except Exception:
                current_ar = False
            auto_reverse_full = st.toggle(
                "Auto reverse full chapter (luồng cũ)",
                value=current_ar,
                key="enable_auto_reverse_full_chapter_toggle",
                help="Bật: sau Bible search, tự load thêm nội dung full chương liên quan entity (📄 CHUONG X: Auto) — tốn token. Tắt (mặc định): chỉ dùng Bible → chunk đã link, không đổ full chương.",
            )
            if st.button("💾 Lưu Auto reverse full chapter", key="save_auto_reverse_full"):
                try:
                    val_ar = "1" if auto_reverse_full else "0"
                    supabase.table("settings").upsert({"key": "enable_auto_reverse_full_chapter", "value": val_ar}, on_conflict="key").execute()
                    st.toast("Đã lưu.")
                except Exception as e:
                    st.error(str(e))
            st.divider()
            try:
                r2 = supabase.table("settings").select("value").eq("key", "max_llm_calls_per_turn").execute()
                max_llm_val = 5
                if r2.data and r2.data[0] is not None:
                    v = r2.data[0].get("value")
                    if v is not None:
                        max_llm_val = max(0, int(v) if isinstance(v, (int, float)) else int(str(v).strip() or "5"))
            except Exception:
                max_llm_val = 5
            max_llm_calls = st.number_input(
                "Số lần gọi LLM tối đa mỗi turn (chỉ tính intent, planner, draft, numerical; verification/check không tính). 0 = không giới hạn.",
                min_value=0,
                max_value=20,
                value=max_llm_val,
                step=1,
                key="max_llm_calls_per_turn_input",
            )
            if st.button("💾 Lưu giới hạn LLM/turn", key="save_max_llm"):
                try:
                    supabase.table("settings").upsert(
                        {"key": "max_llm_calls_per_turn", "value": max_llm_calls},
                        on_conflict="key",
                    ).execute()
                    st.toast("Đã lưu.")
                except Exception as e:
                    st.error(str(e))
            st.divider()
            st.caption("Observability: mỗi turn chat ghi log vào bảng **chat_turn_logs** (intent, context_needs, context_tokens, llm_calls_count). Chạy migration V8.3 để tạo bảng.")
        else:
            st.warning("Chưa kết nối Supabase. Không thể lưu cài đặt V8.")
