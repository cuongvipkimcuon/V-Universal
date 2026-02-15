# Cache helpers: st.cache_data với TTL 5 phút. Invalidate bằng update_trigger (tham số thứ 2).
# Sau khi xóa/ghi DB: gọi invalidate_cache() (chỉ tăng trigger, không rerun). User bấm Refresh để xem mới.
import streamlit as st


@st.cache_data(ttl=300)
def get_chapters_cached(project_id: str, update_trigger: int = 0):
    """Danh sách chapter đầy đủ cho project. update_trigger thay đổi -> cache miss."""
    if not project_id:
        return []
    try:
        from config import init_services
        services = init_services()
        if not services:
            return []
        r = (
            services["supabase"]
            .table("chapters")
            .select("*")
            .eq("story_id", project_id)
            .order("chapter_number")
            .execute()
        )
        return list(r.data) if r.data else []
    except Exception:
        return []


@st.cache_data(ttl=300)
def get_bible_list_cached(project_id: str, update_trigger: int = 0):
    """Toàn bộ story_bible cho project (để list/filter). update_trigger thay đổi -> cache miss."""
    if not project_id:
        return []
    try:
        from config import init_services
        services = init_services()
        if not services:
            return []
        r = (
            services["supabase"]
            .table("story_bible")
            .select("*")
            .eq("story_id", project_id)
            .order("created_at", desc=True)
            .execute()
        )
        return list(r.data) if r.data else []
    except Exception:
        return []


@st.cache_data(ttl=300)
def get_chapter_content_cached(project_id: str, chapter_number: int, update_trigger: int = 0):
    """Một chương (chapters row) theo project_id + chapter_number. Cho Review / Data Analyze."""
    if not project_id or chapter_number is None:
        return None
    try:
        from config import init_services
        services = init_services()
        if not services:
            return None
        r = (
            services["supabase"]
            .table("chapters")
            .select("*")
            .eq("story_id", project_id)
            .eq("chapter_number", chapter_number)
            .limit(1)
            .execute()
        )
        if r.data and len(r.data) > 0:
            return dict(r.data[0])
        return None
    except Exception:
        return None


def invalidate_cache():
    """Sau khi xóa/ghi DB: chỉ tăng update_trigger (lần chạy sau sẽ cache miss). Không clear cache, không rerun. User bấm Refresh nếu muốn xem ngay."""
    st.session_state["update_trigger"] = st.session_state.get("update_trigger", 0) + 1


def invalidate_cache_and_rerun():
    """Deprecated: dùng invalidate_cache() thay vì rerun. Giữ lại để tương thích import."""
    invalidate_cache()


def full_refresh():
    """Xóa toàn bộ cache và rerun app. Chỉ gọi từ nút Refresh (sidebar)."""
    st.cache_data.clear()
    st.session_state["update_trigger"] = st.session_state.get("update_trigger", 0) + 1
    st.rerun()


@st.cache_data(ttl=60)
def get_user_projects_cached(user_id: str, user_email: str, update_trigger: int = 0):
    """Danh sách project của user (owner + shared). Invalidate khi tạo/xóa project (invalidate_cache)."""
    if not user_id and not user_email:
        return []
    try:
        from utils.auth_manager import get_user_projects
        return get_user_projects(user_id or "", user_email or "")
    except Exception:
        return []


@st.cache_data(ttl=60)
def get_user_budget_cached(user_id: str, update_trigger: int = 0):
    """Budget user (credits). Invalidate sau khi dùng credit (invalidate_cache) hoặc Refresh."""
    if not user_id:
        return {"total_credits": 100.0, "used_credits": 0.0, "remaining_credits": 100.0}
    try:
        from config import CostManager
        return CostManager.get_user_budget(user_id)
    except Exception:
        return {"total_credits": 100.0, "used_credits": 0.0, "remaining_credits": 100.0}


@st.cache_data(ttl=300)
def get_dashboard_metrics_cached(project_id: str, update_trigger: int = 0):
    """Dashboard: file_count, bible_count, rule_count, chat_count, recent_files, bible_prefix_list."""
    if not project_id:
        return {}
    try:
        from config import init_services
        services = init_services()
        if not services:
            return {}
        supabase = services["supabase"]
        files = supabase.table("chapters").select("count", count="exact").eq("story_id", project_id).execute()
        bible = supabase.table("story_bible").select("count", count="exact").eq("story_id", project_id).execute()
        rules = supabase.table("story_bible").select("count", count="exact").eq("story_id", project_id).ilike("entity_name", "%[RULE]%").execute()
        chat = supabase.table("chat_history").select("count", count="exact").eq("story_id", project_id).execute()
        recent = supabase.table("chapters").select("title, updated_at").eq("story_id", project_id).order("updated_at", desc=True).limit(5).execute()
        bible_entities = supabase.table("story_bible").select("entity_name").eq("story_id", project_id).execute()
        file_count = files.count if hasattr(files, "count") else len(files.data or [])
        bible_count = bible.count if hasattr(bible, "count") else len(bible.data or [])
        rule_count = rules.count if hasattr(rules, "count") else len(rules.data or [])
        chat_count = chat.count if hasattr(chat, "count") else len(chat.data or [])
        return {
            "file_count": file_count,
            "bible_count": bible_count,
            "rule_count": rule_count,
            "chat_count": chat_count,
            "recent_files": list(recent.data) if recent.data else [],
            "bible_entity_names": [x.get("entity_name") for x in (bible_entities.data or [])],
        }
    except Exception:
        return {}
