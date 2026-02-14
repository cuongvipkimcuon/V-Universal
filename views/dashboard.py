import re
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st

from config import init_services
from utils.cache_helpers import get_dashboard_metrics_cached


def _clean_crystallize_for_user(supabase, story_id, user_id):
    """Khi xóa hết chat: xóa Bible [CHAT] đã crystallize của user, xóa log crystallize, reset counter."""
    try:
        # Lấy các bible_entry_id do user này crystallize (log có thể lưu UUID hoặc BIGINT tùy schema)
        r = supabase.table("chat_crystallize_log").select("bible_entry_id").eq(
            "story_id", story_id
        ).eq("user_id", user_id).execute()
        ids_to_delete = []
        if r.data:
            for row in r.data:
                bid = row.get("bible_entry_id")
                if bid is not None:
                    ids_to_delete.append(bid)
        for bid in ids_to_delete:
            try:
                supabase.table("story_bible").delete().eq("id", bid).eq(
                    "story_id", story_id
                ).execute()
            except Exception:
                pass
        supabase.table("chat_crystallize_log").delete().eq(
            "story_id", story_id
        ).eq("user_id", user_id).execute()
        now = datetime.now(timezone.utc).isoformat()
        supabase.table("chat_crystallize_state").upsert({
            "story_id": story_id,
            "user_id": user_id,
            "messages_since_crystallize": 0,
            "updated_at": now,
        }, on_conflict="story_id,user_id").execute()
    except Exception as e:
        print(f"_clean_crystallize_for_user error: {e}")


def render_dashboard_tab(project_id):
    """Tab Dashboard - Cache metrics, không query DB trong vòng lặp."""
    st.header("📊 Project Dashboard")

    if not project_id:
        st.info("📁 Please select or create a project first")
        return

    services = init_services()
    if not services:
        st.warning("Không kết nối được dịch vụ.")
        return
    supabase = services["supabase"]
    metrics = get_dashboard_metrics_cached(project_id, st.session_state.get("update_trigger", 0))
    file_count = metrics.get("file_count", 0)
    bible_count = metrics.get("bible_count", 0)
    rule_count = metrics.get("rule_count", 0)
    chat_count = metrics.get("chat_count", 0)
    recent_files = metrics.get("recent_files", [])
    bible_entity_names = metrics.get("bible_entity_names", [])

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.markdown(f"""<div class='dashboard-widget'><div class='widget-header'><div class='widget-title'>📄 Files</div></div><div class='widget-value'>{file_count}</div></div>""", unsafe_allow_html=True)
    with col2:
        st.markdown(f"""<div class='dashboard-widget'><div class='widget-header'><div class='widget-title'>📚 Bible Entries</div></div><div class='widget-value'>{bible_count}</div></div>""", unsafe_allow_html=True)
    with col3:
        st.markdown(f"""<div class='dashboard-widget'><div class='widget-header'><div class='widget-title'>📏 Rules</div></div><div class='widget-value'>{rule_count}</div></div>""", unsafe_allow_html=True)
    with col4:
        st.markdown(f"""<div class='dashboard-widget'><div class='widget-header'><div class='widget-title'>💬 Chat Messages</div></div><div class='widget-value'>{chat_count}</div></div>""", unsafe_allow_html=True)

    st.markdown("---")
    col_left, col_right = st.columns([2, 1])
    with col_left:
        st.subheader("📈 Recent Activity")
        if recent_files:
            df_files = pd.DataFrame(recent_files)
            if "updated_at" in df_files.columns:
                df_files["updated_at"] = pd.to_datetime(df_files["updated_at"]).dt.strftime("%Y-%m-%d %H:%M")
            st.dataframe(
                df_files.rename(columns={"title": "File", "updated_at": "Last Updated"}),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("No files yet")

    with col_right:
        st.subheader("🚀 Quick Actions")
        if st.button("📥 Import Bible from Files", use_container_width=True, key="dash_import_bible"):
            st.session_state["import_bible_mode"] = True
        confirm_clean = st.checkbox(
            "Tôi chắc chắn muốn xóa TOÀN BỘ lịch sử chat và điểm nhớ [CHAT] (crystallize) của tôi",
            key="dash_confirm_clean_chats",
            help="Xóa chat_history + Bible [CHAT] đã crystallize + reset counter crystallize. Không hoàn tác được.",
        )
        if st.button("🧹 Clean ALL Chats", use_container_width=True, key="dash_clean_chats"):
            if not confirm_clean:
                st.warning("Vui lòng tick xác nhận trước khi xóa toàn bộ chat.")
            else:
                try:
                    user = st.session_state.get("user")
                    user_id = getattr(user, "id", None) if user else None
                    # 1) Xóa lịch sử chat của user trong dự án
                    q = supabase.table("chat_history").delete().eq("story_id", project_id)
                    if user_id:
                        q = q.eq("user_id", str(user_id))
                    q.execute()
                    # 2) Xóa Bible [CHAT] đã crystallize từ chat của user này + reset crystallize state
                    if user_id:
                        _clean_crystallize_for_user(supabase, project_id, str(user_id))
                    st.success("✅ Đã xóa lịch sử chat và điểm nhớ [CHAT] (crystallize) của bạn trong dự án.")
                    st.cache_data.clear()
                    st.session_state["update_trigger"] = st.session_state.get("update_trigger", 0) + 1
                    st.rerun()
                except Exception as e:
                    st.error(f"Lỗi khi xóa chat: {e}")
        if st.button("🔄 Re-index Bible", use_container_width=True, key="dash_reindex"):
            st.info("Re-indexing would update all embeddings")
        if st.button("📤 Export Project", use_container_width=True, key="dash_export"):
            st.info("Export functionality would be implemented here")

    st.markdown("---")
    st.subheader("📊 Bible Statistics")
    if bible_entity_names:
        prefixes = {}
        for entity_name in bible_entity_names:
            match = re.match(r"^(\[[^\]]+\])", entity_name or "")
            if match:
                prefix = match.group(1)
                prefixes[prefix] = prefixes.get(prefix, 0) + 1
            else:
                prefixes["[OTHER]"] = prefixes.get("[OTHER]", 0) + 1
        if prefixes:
            df_prefix = pd.DataFrame({"Prefix": list(prefixes.keys()), "Count": list(prefixes.values())}).sort_values("Count", ascending=False)
            st.bar_chart(df_prefix.set_index("Prefix"))
        else:
            st.info("No prefix data available")
    else:
        st.info("No bible entries yet")

    st.markdown("---")
    st.header("⚙️ Project Settings")

    col_rename, col_danger = st.columns([2, 3])

    with col_rename:
        st.subheader("✏️ Rename Project")
        current_name = st.session_state.current_project.get('title', 'Untitled')
        new_name = st.text_input("New Project Name", value=current_name)

        if st.button("Update Name", use_container_width=True):
            if new_name and new_name != current_name:
                try:
                    supabase.table("stories").update({
                        "title": new_name
                    }).eq("id", project_id).execute()

                    st.session_state.current_project['title'] = new_name
                    st.success("Project renamed successfully!")
                    time.sleep(1)
                    st.rerun()
                except Exception as e:
                    st.error(f"Error renaming: {e}")

    with col_danger:
        st.subheader("💀 Danger Zone")
        st.warning("Delete this project and ALL associated data (Chapters, Bible, Chat).")

        if not st.session_state.get('confirm_delete_project'):
            if st.button("💣 Delete Project", type="primary", use_container_width=True):
                st.session_state['confirm_delete_project'] = True
                st.rerun()
        else:
            st.error("⚠️ Are you sure? This cannot be undone!")
            c1, c2 = st.columns(2)

            with c1:
                if st.button("❌ Cancel", use_container_width=True):
                    st.session_state['confirm_delete_project'] = False
                    st.rerun()

            with c2:
                if st.button("✅ YES, DELETE", type="primary", use_container_width=True):
                    try:
                        supabase.table("stories").delete().eq("id", project_id).execute()

                        st.success("Project deleted!")

                        st.session_state['current_project'] = None
                        st.session_state['project_id'] = None
                        st.session_state['confirm_delete_project'] = False

                        time.sleep(1.5)
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error deleting: {e}")
