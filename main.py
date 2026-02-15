"""
V-Universe AI Hub Pro Ver 7.0 - Entry point.
Tối ưu: lazy import view, chỉ render 1 tab đang chọn, cache sidebar/cost.
"""
import importlib
import streamlit as st
import time

from config import Config, init_services, SessionManager
from utils.cache_helpers import get_user_budget_cached
from views.sidebar import render_sidebar

# ==========================================
# PAGE CONFIG & CSS
# ==========================================
st.set_page_config(
    page_title="V-Universe AI Hub Pro Ver 7.0",
    page_icon="✦",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
<style>
    .main .block-container { padding-top: 1.25rem; padding-bottom: 1.25rem; max-width: 1400px; }
    [data-testid="stSidebar"] .stButton > button {
        background: linear-gradient(135deg, #6d28d9 0%, #7c3aed 100%);
        color: white; border: none; border-radius: 8px; font-weight: 500; transition: opacity 0.2s, transform 0.05s;
    }
    [data-testid="stSidebar"] .stButton > button:hover { opacity: 0.92; }
    .stTabs [data-baseweb="tab-list"] { gap: 6px; background: #f8fafc; padding: 8px; border-radius: 10px; }
    .stTabs [aria-selected="true"] { background: linear-gradient(135deg, #6d28d9 0%, #7c3aed 100%); color: white; border-radius: 8px; }
    .danger-zone { border: 1px solid #fecaca; border-radius: 10px; padding: 16px; margin-top: 16px; background: #fef2f2; }
    .stExpander { border-radius: 8px; border: 1px solid #e2e8f0; }
    .stExpander summary { font-weight: 600; }
    div[data-testid="stHorizontalBlock"] > div { padding: 0 0.4rem; }
    .stSubheader, h3 { color: #334155; }
    .dashboard-widget { background: #fff; border-radius: 10px; padding: 16px; border: 1px solid #e2e8f0; box-shadow: 0 1px 2px rgba(0,0,0,0.04); }
    .widget-value { font-size: 1.75rem; font-weight: 700; color: #334155; }
    .widget-title { font-size: 0.9rem; color: #64748b; }
    [data-testid="stTextInput"] input, [data-testid="stTextInput"] input:focus { border-radius: 8px; border-color: #e2e8f0; }
    [data-testid="stTextInput"] input:focus { box-shadow: 0 0 0 2px rgba(124, 58, 237, 0.2); border-color: #7c3aed; }
    .stButton > button { border-radius: 8px; font-weight: 500; transition: opacity 0.2s; }
    .stButton > button:hover { opacity: 0.9; }
    div[data-testid="stHorizontalBlock"] > label[data-testid="stRadio"] > div { gap: 6px; background: #f8fafc; padding: 8px; border-radius: 10px; flex-wrap: wrap; }
    div[data-testid="stHorizontalBlock"] > label[data-testid="stRadio"] div[role="radiogroup"] { flex-direction: row; gap: 6px; }
</style>
""", unsafe_allow_html=True)

# Sub-tabs mapping: main_tab -> [(sub_id, label, render_fn, needs_persona)]
TAB_STRUCTURE = {
    "workspace": [
        ("dashboard", "📊 Dashboard", "render_dashboard_tab", False),
        ("workstation", "✍️ Workstation", "render_workstation_tab", True),
        ("data_analyze", "📊 Data Analyze", "render_data_analyze_tab", False),
        ("background_tasks", "🔄 Background Jobs", "render_background_tasks_tab", False),
        ("review", "🤖 Review", "render_review_tab", False),
        ("python_executor", "🧮 Python Executor", "render_python_executor_tab", False),
    ],
    "knowledge": [
        ("bible", "📖 Bible", "render_bible_tab", True),
        ("relations", "🔗 Relations", "render_relations_tab", True),
        ("chunking", "✂️ Chunking", "render_chunking_tab", False),
        ("rules", "📋 Rules", "render_rules_tab", True),
        ("chat_mgmt", "💬 Memory", "render_chat_management_tab", True),
        ("arc", "📐 Arc", "render_arc_tab", False),
        ("timeline", "📅 Timeline", "render_timeline_tab", False),
        ("commands", "📌 Chỉ lệnh", "render_commands_tab", True),
        ("data_health", "🛡️ Data Health", "render_data_health_tab", False),
        ("semantic_intent", "🎯 Semantic Intent", "render_semantic_intent_tab", False),
    ],
    "chat": [
        ("v_work", "🔧 V Work", "render_chat_tab", True),
        ("v_home", "🏠 V Home", "render_chat_tab", False),
    ],
    "admin": [
        ("collaboration", "👥 Collaboration", "render_collaboration_tab", False),
        ("cost", "💰 Cost", "render_cost_tab", False),
        ("settings", "⚙️ Settings", "render_settings_tab", False),
    ],
}

# Lazy load: chỉ import view khi cần (giảm lag mỗi run)
VIEW_MODULES = {
    "render_dashboard_tab": "views.dashboard",
    "render_workstation_tab": "views.workstation",
    "render_data_analyze_tab": "views.data_analyze",
    "render_background_tasks_tab": "views.background_tasks_tab",
    "render_review_tab": "views.review",
    "render_python_executor_tab": "views.python_executor_view",
    "render_bible_tab": "views.bible",
    "render_rules_tab": "views.rules_view",
    "render_chat_management_tab": "views.chat_management_view",
    "render_relations_tab": "views.relations_view",
    "render_chunking_tab": "views.chunking_view",
    "render_arc_tab": "views.arc_view",
    "render_timeline_tab": "views.timeline_view",
    "render_commands_tab": "views.commands_tab",
    "render_data_health_tab": "views.data_health",
    "render_semantic_intent_tab": "views.semantic_intent_view",
    "render_chat_tab": "views.chat",
    "render_collaboration_tab": "views.collaboration",
    "render_cost_tab": "views.cost",
    "render_settings_tab": "views.settings",
}


def _get_render_fn(fn_name):
    """Import view module khi cần, trả về hàm render."""
    if fn_name not in VIEW_MODULES:
        return None
    mod = importlib.import_module(VIEW_MODULES[fn_name])
    return getattr(mod, fn_name, None)


def main():
    session_manager = SessionManager()

    if st.session_state.get('logging_out'):
        del st.session_state['logging_out']
        session_manager.render_login_form()
        return

    if not session_manager.check_login():
        time.sleep(1)
        session_manager.render_login_form()
        return

    if not Config.validate():
        st.stop()

    services = init_services()
    if not services:
        st.error("Failed to initialize services.")
        st.stop()

    project_id, persona = render_sidebar(session_manager)

    # Header (tiêu đề căn giữa)
    col1, col2 = st.columns([3, 1])
    with col1:
        if st.session_state.get('current_project'):
            st.markdown(
                f"<h1 style='text-align: center; margin: 0; font-size: 1.75rem; font-weight: 600; color: #334155;'>{st.session_state.current_project.get('title', 'Untitled')}</h1>",
                unsafe_allow_html=True
            )
        else:
            st.markdown(
                "<h1 style='text-align: center; margin: 0; font-size: 1.75rem; font-weight: 600; background: linear-gradient(135deg, #5b21b6 0%, #7c3aed 100%); -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text;'>V-Universe AI Hub Pro Ver 7.0</h1>",
                unsafe_allow_html=True
            )
            st.markdown("<p style='text-align: center; color: #64748b; margin-top: 0.25rem; font-size: 0.95rem;'>Select or create a project</p>", unsafe_allow_html=True)
    with col2:
        if 'user' in st.session_state:
            _trigger = st.session_state.get("update_trigger", 0)
            budget = get_user_budget_cached(st.session_state.user.id, _trigger)
            st.metric("Credits", f"${budget.get('remaining_credits', 0):.2f}")

    # Chỉ render 1 tab đang chọn (main + sub) → giảm từ ~20 xuống 1 render mỗi run
    main_labels = ["📂 Workspace", "📚 Knowledge", "💬 Chat", "⚙️ Admin"]
    main_keys = ["workspace", "knowledge", "chat", "admin"]
    st.session_state.setdefault("main_tab_key", "workspace")
    main_idx = st.radio(
        "Tab",
        range(len(main_labels)),
        format_func=lambda i: main_labels[i],
        key="main_tab_radio",
        horizontal=True,
        label_visibility="collapsed",
    )
    main_tab_key = main_keys[max(0, min(int(main_idx) if main_idx is not None else 0, len(main_keys) - 1))]
    st.session_state["main_tab_key"] = main_tab_key

    subs = TAB_STRUCTURE.get(main_tab_key, [])
    if not subs:
        st.info("Chọn tab ở trên.")
    else:
        sub_labels = [s[1] for s in subs]
        radio_key = "sub_%s" % main_tab_key
        sub_idx = st.radio(
            "Sub",
            range(len(sub_labels)),
            format_func=lambda i: sub_labels[i] if i < len(sub_labels) else "",
            key=radio_key,
            horizontal=True,
            label_visibility="collapsed",
        )
        sub_idx = max(0, min(int(sub_idx) if sub_idx is not None else 0, len(subs) - 1))
        sub_id, _, fn_name, needs_persona = subs[sub_idx]
        render_fn = _get_render_fn(fn_name)
        if render_fn:
            try:
                if sub_id in ("v_work", "v_home"):
                    render_fn(project_id, persona, sub_id)
                elif needs_persona:
                    render_fn(project_id, persona)
                elif sub_id in ("cost", "settings"):
                    render_fn()
                else:
                    render_fn(project_id)
            except TypeError:
                try:
                    if sub_id in ("v_work", "v_home"):
                        render_fn(project_id, persona, sub_id)
                    elif sub_id in ("cost", "settings"):
                        render_fn()
                    else:
                        render_fn(project_id)
                except TypeError:
                    render_fn(project_id)

    st.markdown("---")
    st.markdown(
        "<div style='text-align: center; color: #64748b; padding: 16px; font-size: 0.85rem;'>"
        "V-Universe AI Hub Pro • Ver 7.0 • Semantic Intent • Arc • Chunking • Chỉ lệnh @@ • Auto Crystallize"
        "</div>",
        unsafe_allow_html=True
    )


if __name__ == "__main__":
    main()
