"""
V-Universe AI Hub Pro - Entry point.
Điều hướng chính: cấu hình trang, CSS, và gọi các view.
"""
import streamlit as st
import time

from config import Config, init_services, SessionManager, CostManager
from views import (
    render_sidebar,
    render_dashboard_tab,
    render_chat_tab,
    render_workstation_tab,
    render_bible_tab,
    render_cost_tab,
    render_settings_tab,
    render_collaboration_tab,
)

# ==========================================
# 🎨 CẤU HÌNH & CSS NÂNG CẤP
# ==========================================
st.set_page_config(
    page_title="V-Universe AI Hub Pro",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="expanded"
)

# CSS tùy chỉnh nâng cao với màu sắc hiện đại
st.markdown("""
<style>
    /* Sửa màu chữ Tab thành màu trắng và in đậm */
    button[data-baseweb="tab"] {
        color: white !important;
        font-weight: 600 !important;
        background-color: #262730 !important;
        border: 1px solid #444 !important;
        margin-right: 4px !important;
    }

    button[data-baseweb="tab"][aria-selected="true"] {
        background-color: #FF4B4B !important;
        color: white !important;
        border-color: #FF4B4B !important;
    }
    
    div[data-baseweb="tab-highlight"] {
        display: none !important;
    }
</style>
<style>
    .main .block-container {
        padding-top: 1rem;
        padding-bottom: 1rem;
    }
    
    [data-testid="stSidebar"] {
        background: #ffffff !important;
        color: #000000 !important;
    }
    
    [data-testid="stSidebar"] * {
        color: #000000 !important;
    }
    
    [data-testid="stSidebar"] h1,
    [data-testid="stSidebar"] h2,
    [data-testid="stSidebar"] h3,
    [data-testid="stSidebar"] h4,
    [data-testid="stSidebar"] p,
    [data-testid="stSidebar"] label,
    [data-testid="stSidebar"] span,
    [data-testid="stSidebar"] div {
        color: #000000 !important;
    }
    
    [data-testid="stSidebar"] .stButton > button {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        color: white;
        border: none;
        border-radius: 10px;
        padding: 12px 20px;
        font-weight: 600;
        transition: all 0.3s ease;
        width: 100%;
        margin: 5px 0;
    }
    
    [data-testid="stSidebar"] .stButton > button:hover {
        background: linear-gradient(135deg, #5a6fd8 0%, #6a4490 100%);
        transform: translateY(-2px);
        box-shadow: 0 6px 20px rgba(102, 126, 234, 0.4);
    }
    
    [data-testid="stSidebar"] .stSelectbox label,
    [data-testid="stSidebar"] .stTextInput label,
    [data-testid="stSidebar"] .stMetric label,
    [data-testid="stSidebar"] .stInfo {
        color: #000000 !important;
    }
    
    [data-testid="stSidebar"] .stSelectbox > div > div {
        background-color: #ffffff !important;
        color: #000000 !important;
    }
    
    .stTabs [data-baseweb="tab-list"] {
        gap: 8px;
        background: #f8fafc;
        padding: 8px;
        border-radius: 12px;
        margin-bottom: 20px;
    }
    
    .stTabs [data-baseweb="tab"] {
        height: 50px;
        padding: 0 24px;
        background-color: #e2e8f0;
        border-radius: 8px;
        font-weight: 600;
        color: #4a5568;
        border: 2px solid transparent;
        transition: all 0.3s ease;
    }
    
    .stTabs [aria-selected="true"] {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        color: white;
        border-color: #667eea;
        box-shadow: 0 4px 12px rgba(102, 126, 234, 0.3);
    }
    
    .stChatMessage {
        padding: 20px;
        border-radius: 15px;
        margin: 12px 0;
        border-left: 5px solid;
        box-shadow: 0 2px 8px rgba(0,0,0,0.1);
    }
    
    .stChatMessage[data-testid="user"] {
        background: linear-gradient(135deg, #e3f2fd 0%, #bbdefb 100%);
        border-left-color: #2196f3;
    }
    
    .stChatMessage[data-testid="assistant"] {
        background: linear-gradient(135deg, #f1f8e9 0%, #dcedc8 100%);
        border-left-color: #4caf50;
    }
    
    .card {
        background: white;
        border-radius: 15px;
        padding: 20px;
        margin: 10px 0;
        border: 1px solid #e2e8f0;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.05);
        transition: all 0.3s ease;
    }
    
    .card:hover {
        box-shadow: 0 8px 24px rgba(0, 0, 0, 0.1);
        transform: translateY(-3px);
    }
    
    .stTextInput > div > div > input,
    .stTextArea > div > div > textarea {
        border-radius: 10px;
        border: 2px solid #e2e8f0;
        padding: 12px;
        font-size: 14px;
    }
    
    .stTextInput > div > div > input:focus,
    .stTextArea > div > div > textarea:focus {
        border-color: #667eea;
        box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
    }
    
    .metric-container {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
        gap: 15px;
        margin: 20px 0;
    }
    
    .metric-box {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        color: white;
        padding: 20px;
        border-radius: 12px;
        text-align: center;
    }
    
    .metric-value { font-size: 28px; font-weight: 700; margin: 10px 0; }
    .metric-label { font-size: 14px; opacity: 0.9; }
    
    .status-badge {
        display: inline-block;
        padding: 6px 12px;
        border-radius: 20px;
        font-size: 12px;
        font-weight: 600;
        margin: 2px;
    }
    
    .status-active { background: #d4edda; color: #155724; }
    .status-warning { background: #fff3cd; color: #856404; }
    .status-error { background: #f8d7da; color: #721c24; }
    
    @keyframes fadeIn {
        from { opacity: 0; transform: translateY(10px); }
        to { opacity: 1; transform: translateY(0); }
    }
    
    .animate-fadeIn {
        animation: fadeIn 0.5s ease-out;
    }
    
    ::-webkit-scrollbar { width: 8px; height: 8px; }
    ::-webkit-scrollbar-track { background: #f1f1f1; border-radius: 10px; }
    ::-webkit-scrollbar-thumb {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        border-radius: 10px;
    }
    ::-webkit-scrollbar-thumb:hover {
        background: linear-gradient(135deg, #5a6fd8 0%, #6a4490 100%);
    }
    
    h1, h2, h3, h4 { color: #2d3748; font-weight: 700; }
    h1 { font-size: 2.5rem; margin-bottom: 1rem; }
    h2 { font-size: 2rem; margin-bottom: 0.75rem; }
    h3 { font-size: 1.5rem; margin-bottom: 0.5rem; }
    
    hr {
        border: none;
        height: 2px;
        background: linear-gradient(to right, transparent, #667eea, transparent);
        margin: 30px 0;
    }
    
    .dashboard-widget {
        background: white;
        border-radius: 15px;
        padding: 20px;
        margin: 10px 0;
        border: 1px solid #e2e8f0;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.05);
    }
    
    .widget-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 15px;
    }
    
    .widget-title { font-size: 18px; font-weight: 600; color: #2d3748; }
    .widget-value { font-size: 24px; font-weight: 700; color: #667eea; }
</style>
""", unsafe_allow_html=True)


# ==========================================
# 🚀 MAIN APP - ĐIỀU HƯỚNG
# ==========================================
def main():
    """Hàm chính của ứng dụng - chỉ điều hướng và gọi views."""
    session_manager = SessionManager()

    if st.session_state.get('logging_out'):
        if 'logging_out' in st.session_state:
            del st.session_state['logging_out']
        session_manager.render_login_form()
        return

    is_logged_in = session_manager.check_login()

    if not is_logged_in:
        time.sleep(1)
        session_manager.render_login_form()
        return

    if not Config.validate():
        st.stop()

    services = init_services()
    if not services:
        st.error("Failed to initialize services. Please check your configuration.")
        st.stop()

    project_id, persona = render_sidebar(session_manager)

    col1, col2 = st.columns([3, 1])
    with col1:
        if st.session_state.get('current_project'):
            project_name = st.session_state.current_project.get('title', 'Untitled')
            st.title(f"{persona['icon']} {project_name}")
            st.caption(f"{persona['role']} • Project Management")
        else:
            st.title("🚀 V-Universe AI Hub Pro")
            st.caption("Select or create a project to get started")

    with col2:
        if 'user' in st.session_state:
            budget = CostManager.get_user_budget(st.session_state.user.id)
            st.metric("Available Credits", f"${budget.get('remaining_credits', 0):.2f}")

    tabs = st.tabs([
        "📊 Dashboard",
        "💬 Smart Chat",
        "✍️ Workstation",
        "📚 Project Bible",
        "👥 Collaboration",
        "💰 Cost Management",
        "⚙️ Settings",
    ])

    with tabs[0]:
        render_dashboard_tab(project_id)

    with tabs[1]:
        render_chat_tab(project_id, persona)

    with tabs[2]:
        render_workstation_tab(project_id, persona)

    with tabs[3]:
        render_bible_tab(project_id, persona)

    with tabs[4]:
        render_collaboration_tab(project_id)

    with tabs[5]:
        render_cost_tab()

    with tabs[6]:
        render_settings_tab()

    st.markdown("---")
    st.markdown(
        """
        <div style='text-align: center; color: #666; padding: 20px;'>
            <p>🚀 V-Universe AI Hub Pro • Powered by OpenRouter AI & Supabase • v3.0</p>
            <p style='font-size: 12px;'>Hybrid Search • Rule Mining • Strict Mode • 20+ AI models • Intelligent context management</p>
        </div>
        """,
        unsafe_allow_html=True
    )


if __name__ == "__main__":
    main()
