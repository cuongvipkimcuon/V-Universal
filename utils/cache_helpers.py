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
    """Toàn bộ story_bible cho project (để list/filter). Không gồm [RULE]/[CHAT] — dùng get_rules_list_cached/get_chat_crystallize_*. update_trigger thay đổi -> cache miss."""
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
        data = list(r.data) if r.data else []
        # V8.9: Loại [RULE] và [CHAT] — đã chuyển sang bảng riêng
        data = [e for e in data if not (e.get("entity_name") or "").strip().startswith("[RULE]") and not (e.get("entity_name") or "").strip().startswith("[CHAT]")]
        return data
    except Exception:
        return []


@st.cache_data(ttl=300)
def get_rules_list_cached(project_id: str, update_trigger: int = 0):
    """Danh sách rules cho project: từ project_rules (scope global + project + arc).
    Mỗi item: id, scope, content/description, entity_name (label), created_at, approve, source.
    """
    out = []
    try:
        from config import init_services
        services = init_services()
        if not services:
            return []
        supabase = services["supabase"]
        # V8.9+: project_rules — global (story_id NULL) + project/arc (story_id = project_id); UI cần thấy cả đã duyệt và chưa duyệt
        try:
            r_global = supabase.table("project_rules").select("*").eq("scope", "global").order("created_at", desc=True).execute()
            for row in (r_global.data or []):
                out.append({
                    "id": row.get("id"),
                    "scope": "global",
                    "content": row.get("content", ""),
                    "description": row.get("content", ""),
                    "entity_name": f"[RULE] (global) {row.get('content', '')[:50]}",
                    "created_at": row.get("created_at"),
                    "approve": row.get("approve", True),
                    "source": "project_rules",
                })
        except Exception:
            pass
        if project_id:
            # Project-level rules
            try:
                r_proj = (
                    supabase.table("project_rules")
                    .select("*")
                    .eq("scope", "project")
                    .eq("story_id", project_id)
                    .order("created_at", desc=True)
                    .execute()
                )
                for row in (r_proj.data or []):
                    out.append({
                        "id": row.get("id"),
                        "scope": "project",
                        "content": row.get("content", ""),
                        "description": row.get("content", ""),
                        "entity_name": f"[RULE] {row.get('content', '')[:50]}",
                        "created_at": row.get("created_at"),
                        "approve": row.get("approve", True),
                        "source": "project_rules",
                    })
            except Exception:
                pass

            # V9.3+: rules cấp ARC (scope = 'arc' + story_id, map nhiều arc qua project_rule_arcs)
            try:
                r_arc_rules = (
                    supabase.table("project_rules")
                    .select("*")
                    .eq("scope", "arc")
                    .eq("story_id", project_id)
                    .order("created_at", desc=True)
                    .execute()
                )
                arc_rules = list(r_arc_rules.data or [])
                if arc_rules:
                    # Lấy map rule_id -> danh sách arc_id từ project_rule_arcs
                    rule_ids = [row.get("id") for row in arc_rules if row.get("id")]
                    arc_map = {}
                    if rule_ids:
                        try:
                            m = (
                                supabase.table("project_rule_arcs")
                                .select("rule_id, arc_id")
                                .in_("rule_id", rule_ids)
                                .execute()
                            )
                            for r in (m.data or []):
                                rid = r.get("rule_id")
                                aid = r.get("arc_id")
                                if not rid or not aid:
                                    continue
                                arc_map.setdefault(str(rid), set()).add(str(aid))
                        except Exception:
                            arc_map = {}

                    # Lấy tên arc để hiển thị label đẹp hơn
                    all_arc_ids = set()
                    for s in arc_map.values():
                        all_arc_ids.update(s)
                    arc_name_map = {}
                    if all_arc_ids:
                        try:
                            ar = (
                                supabase.table("arcs")
                                .select("id, name")
                                .in_("id", list(all_arc_ids))
                                .execute()
                            )
                            for row in (ar.data or []):
                                arc_name_map[str(row.get("id"))] = (row.get("name") or "").strip() or "Arc"
                        except Exception:
                            arc_name_map = {}

                    for row in arc_rules:
                        rid = row.get("id")
                        base_arc_id = row.get("arc_id")
                        # Hợp nhất: arc_id cũ + map nhiều arc
                        arc_ids = set()
                        if base_arc_id:
                            arc_ids.add(str(base_arc_id))
                        arc_ids.update(arc_map.get(str(rid), set()))
                        arc_ids_list = list(arc_ids)
                        # Tên arc cho label
                        arc_names = [arc_name_map.get(aid, "") for aid in arc_ids_list if arc_name_map.get(aid)]
                        arc_label = ", ".join(arc_names) if arc_names else ""
                        label_prefix = "[RULE][arc]"
                        if arc_label:
                            label_prefix = f"{label_prefix} ({arc_label})"
                        out.append({
                            "id": rid,
                            "scope": "arc",
                            "arc_ids": arc_ids_list,
                            "content": row.get("content", ""),
                            "description": row.get("content", ""),
                            "entity_name": f"{label_prefix} {row.get('content', '')[:50]}",
                            "created_at": row.get("created_at"),
                            "approve": row.get("approve", True),
                            "source": "project_rules",
                        })
            except Exception:
                pass
        out.sort(key=lambda x: (x.get("created_at") or ""), reverse=True)
        return out
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
        # V9.2+: rule_count = project_rules đã approve (global + project)
        rule_count = 0
        try:
            r_global = (
                supabase.table("project_rules")
                .select("id")
                .eq("scope", "global")
                .eq("approve", True)
                .execute()
            )
            r_proj = (
                supabase.table("project_rules")
                .select("id")
                .eq("scope", "project")
                .eq("story_id", project_id)
                .eq("approve", True)
                .execute()
            )
            rule_count = len(r_global.data or []) + len(r_proj.data or [])
        except Exception:
            rule_count = 0
        chat = supabase.table("chat_history").select("count", count="exact").eq("story_id", project_id).execute()
        recent = supabase.table("chapters").select("title, updated_at").eq("story_id", project_id).order("updated_at", desc=True).limit(5).execute()
        bible_entities = supabase.table("story_bible").select("entity_name").eq("story_id", project_id).execute()
        file_count = files.count if hasattr(files, "count") else len(files.data or [])
        bible_count = bible.count if hasattr(bible, "count") else len(bible.data or [])
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
