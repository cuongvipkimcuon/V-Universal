import time
import streamlit as st

from config import Config, init_services, CostManager
from persona import PersonaSystem
from utils.auth_manager import get_user_projects


def render_sidebar(session_manager):
    """Render sidebar với thông tin user và project"""
    with st.sidebar:
        st.markdown("🚀 V-Universe AI Pro", unsafe_allow_html=True)
        if 'user' in st.session_state and st.session_state.user:
            user_email = st.session_state.user.email
            st.markdown(f"_{user_email.split('@')}_", unsafe_allow_html=True)

            budget = CostManager.get_user_budget(st.session_state.user.id)
            col1, col2 = st.columns(2)
            with col1:
                st.metric("💰 Credits", f"${budget.get('remaining_credits', 0):.2f}")
            with col2:
                usage_percent = (budget.get('used_credits', 0) / budget.get('total_credits', 100)) * 100
                st.metric("Usage", f"{usage_percent:.1f}%")
            st.markdown("---")

        st.subheader("📂 Projects")
        services = init_services()
        supabase = services['supabase']

        if 'user' in st.session_state and st.session_state.user:
            projects = get_user_projects(st.session_state.user.id, st.session_state.user.email)
        else:
            projects = []

        proj_id = None
        persona = PersonaSystem.PERSONAS["Writer"]

        if projects:
            labels = []
            for p in projects:
                title = p.get("title") or "Untitled"
                role = p.get("role") or "owner"
                if role == "owner":
                    labels.append(title)
                else:
                    labels.append(f"{title} (Shared)")
            idx = st.selectbox(
                "Select Project",
                range(len(projects)),
                format_func=lambda i: labels[i] if i < len(labels) else "",
                key="project_selector"
            )
            current_proj = projects[idx]
            proj_id = current_proj["id"]
            proj_type = current_proj.get("category", "Writer")

            st.session_state["current_project"] = current_proj
            st.session_state["project_id"] = proj_id
            st.session_state["persona"] = proj_type

            persona = PersonaSystem.get_persona(proj_type)
            st.info(f"{persona['icon']} **{proj_type} Mode**")

        st.markdown("---")
        if st.button("Create New Project", type="primary"):
            st.session_state['show_new_project'] = True

        if st.session_state.get('show_new_project'):
            with st.form("new_project_form"):
                title = st.text_input("Project Name")
                category = st.selectbox("Category", PersonaSystem.get_available_personas())

                if st.form_submit_button("Create"):
                    if title:
                        supabase.table("stories").insert({
                            "title": title,
                            "category": category,
                            "user_id": st.session_state.user.id
                        }).execute()
                        st.success("Project created!")
                        st.session_state['show_new_project'] = False
                        st.rerun()

        st.markdown("---")
        st.subheader("🤖 AI Settings")

        model_category = st.selectbox(
            "Model Category",
            list(Config.AVAILABLE_MODELS.keys()),
            index=1,
            key="model_category"
        )

        available_models = Config.AVAILABLE_MODELS[model_category]
        selected_model = st.selectbox(
            "Select Model",
            available_models,
            index=0,
            key="model_selector"
        )
        st.session_state['selected_model'] = selected_model

        with st.expander("Advanced Settings"):
            st.session_state['temperature'] = st.slider(
                "Temperature",
                min_value=0.0, max_value=1.0,
                value=persona.get('temperature', 0.7),
                step=0.1
            )
            st.session_state['context_size'] = st.select_slider(
                "Context Size",
                options=["low", "medium", "high", "max"],
                value="medium"
            )

        st.markdown("---")

        st.subheader("⚡ Quick Actions")
        if st.button("🔄 Refresh Session", use_container_width=True):
            st.rerun()

        st.markdown("---")
        if st.button("🚪 Logout", use_container_width=True, type="secondary"):
            st.session_state['logging_out'] = True
            try:
                session_manager.cookie_manager.delete("supabase_access_token", key="del_access_logout")
                session_manager.cookie_manager.delete("supabase_refresh_token", key="del_refresh_logout")
            except Exception:
                pass

            for key in list(st.session_state.keys()):
                if key != 'logging_out':
                    del st.session_state[key]

            st.success("Logged out successfully!")
            time.sleep(1)
            st.rerun()

        return proj_id, persona
