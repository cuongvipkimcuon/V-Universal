import pandas as pd
import streamlit as st

from config import Config, init_services
from utils.cache_helpers import get_user_budget_cached


def render_cost_tab():
    """Tab Cost Management"""
    st.header("💰 Cost Management")

    if 'user' not in st.session_state:
        st.warning("Please login")
        return

    user_id = st.session_state.user.id
    _trigger = st.session_state.get("update_trigger", 0)
    budget = get_user_budget_cached(user_id, _trigger)

    col1, col2, col3 = st.columns(3)

    with col1:
        st.metric(
            "Total Credits",
            f"${budget.get('total_credits', 0):.2f}"
        )

    with col2:
        st.metric(
            "Used Credits",
            f"${budget.get('used_credits', 0):.2f}",
            delta=f"-${budget.get('used_credits', 0):.2f}"
        )

    with col3:
        remaining = budget.get('remaining_credits', 0)
        st.metric(
            "Remaining",
            f"${remaining:.2f}"
        )

    usage_percent = (budget.get('used_credits', 0) / budget.get('total_credits', 100)) * 100
    st.progress(min(usage_percent / 100, 1.0))

    st.markdown("---")
    st.subheader("📊 Model Cost Comparison")

    model_costs = []
    for model, costs in Config.MODEL_COSTS.items():
        if model in [m for models in Config.AVAILABLE_MODELS.values() for m in models]:
            avg_cost = (costs['input'] + costs['output']) / 2
            model_costs.append({
                "Model": model.split('/')[-1],
                "Input Cost": f"${costs['input']}/M",
                "Output Cost": f"${costs['output']}/M",
                "Avg Cost": f"${avg_cost:.2f}/M"
            })

    model_costs.sort(key=lambda x: float(x['Avg Cost'].replace('$', '').replace('/M', '')))

    df = pd.DataFrame(model_costs)
    st.dataframe(df, use_container_width=True, hide_index=True)

    st.markdown("---")
    st.subheader("📈 Usage History")

    try:
        services = init_services()
        supabase = services['supabase']

        chat_history = supabase.table("chat_history") \
            .select("created_at, metadata") \
            .eq("story_id", st.session_state.get('project_id', '')) \
            .order("created_at", desc=True) \
            .limit(100) \
            .execute()

        if chat_history.data:
            costs = []
            for chat in chat_history.data:
                if chat.get('metadata') and 'cost' in chat['metadata']:
                    try:
                        cost_str = chat['metadata']['cost']
                        if cost_str.startswith('$'):
                            cost = float(cost_str[1:])
                            costs.append({
                                'date': chat['created_at'][:10],
                                'cost': cost
                            })
                    except Exception:
                        pass

            if costs:
                df_costs = pd.DataFrame(costs)
                df_grouped = df_costs.groupby('date').sum().reset_index()
                st.line_chart(df_grouped.set_index('date'))
            else:
                st.info("No cost data available in recent history")
        else:
            st.info("No chat history available")

    except Exception as e:
        st.error(f"Error loading usage history: {e}")

    st.markdown("---")
    st.subheader("💳 Add Credits")

    with st.form("add_credits"):
        amount = st.select_slider(
            "Amount to add",
            options=[10, 25, 50, 100, 200, 500],
            value=50
        )

        if st.form_submit_button("Add Credits", type="primary"):
            st.info(f"💳 Payment integration would add ${amount} to your account")
            st.info("For now, credits are simulated")
