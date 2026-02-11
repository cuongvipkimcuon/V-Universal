import streamlit as st

from config import Config, init_services
from .setup_tabs import render_prefix_setup, render_persona_setup


def render_settings_tab():
    """Tab Settings — hợp nhất Cấu hình AI, Quản lý Tiền tố Bible, Cấu hình Personas (V5.1)."""
    st.header("⚙️ Settings")

    tab1, tab2, tab3, tab4 = st.tabs([
        "👤 Account",
        "🤖 Cấu hình AI",
        "🎨 Giao diện",
        "📋 Bible & Personas",
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
        st.subheader("🤖 Cấu hình AI")
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
                if "[RULE]" not in prefixes:
                    prefixes.append("[RULE]")
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

    with tab3:
        st.subheader("🎨 Giao diện")
        theme = st.selectbox("Theme", ["Light", "Dark", "Auto"], index=2, help="Giao diện sáng/tối/tự động.")
        font_size = st.select_slider("Cỡ chữ", options=["Small", "Medium", "Large"], value="Medium")
        chat_density = st.select_slider("Mật độ Chat", options=["Compact", "Comfortable", "Spacious"], value="Comfortable")
        if st.button("✅ Áp dụng giao diện", type="primary"):
            st.success("Đã áp dụng (có thể cần refresh trang).")

    with tab4:
        st.caption("Quản lý Tiền tố Bible (bảng prefix) và Personas (phong cách AI).")
        with st.expander("📋 Quản lý Tiền tố Bible", expanded=True):
            render_prefix_setup()
        with st.expander("🎭 Cấu hình Personas", expanded=False):
            render_persona_setup()
