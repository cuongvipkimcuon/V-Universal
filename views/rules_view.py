# views/rules_view.py - Vùng Rules (chỉ [RULE] từ Bible)
"""Hiển thị và quản lý Rules. Thêm/sửa: tự tạo vector. Danger Zone xóa sạch."""
import re
import streamlit as st

from config import Config, init_services
from ai_engine import AIService
from utils.auth_manager import check_permission
from utils.cache_helpers import get_rules_list_cached, invalidate_cache, full_refresh
from core.background_jobs import run_rules_embedding_backfill, is_embedding_backfill_running

KNOWLEDGE_PAGE_SIZE = 10


def render_rules_tab(project_id, persona):
    st.header("📋 Rules")
    st.caption("V9.2: Quy tắc theo phạm vi (global / project / arc) và loại (Style / Method / Info / Unknown). Mặc định lưu cấp project, type = Unknown.")

    if not project_id:
        st.info("📁 Chọn Project trước.")
        return

    st.session_state.setdefault("update_trigger", 0)
    services = init_services()
    if not services:
        st.warning("Không kết nối được dịch vụ.")
        return
    supabase = services["supabase"]
    user = st.session_state.get("user")
    user_id = getattr(user, "id", None) if user else None
    user_email = getattr(user, "email", None) if user else None
    can_write = check_permission(str(user_id or ""), user_email or "", project_id, "write")
    can_delete = check_permission(str(user_id or ""), user_email or "", project_id, "delete")

    # Thống kê embedding cho Rules cấp project (chỉ tính rule đã duyệt)
    try:
        r_all = (
            supabase.table("project_rules")
            .select("id, approve")
            .eq("story_id", project_id)
            .execute()
        )
        all_rows = r_all.data or []
        approved_ids = [row["id"] for row in all_rows if row.get("id") and bool(row.get("approve", False))]
        approved_total = len(approved_ids)
        if approved_ids:
            r_null = (
                supabase.table("project_rules")
                .select("id")
                .in_("id", approved_ids)
                .is_("embedding", "NULL")
                .execute()
            )
            need_embed = len(r_null.data or [])
        else:
            need_embed = 0
        embedded_count = max(0, approved_total - need_embed)
    except Exception:
        approved_total = 0
        embedded_count = 0
        need_embed = 0

    st.caption(f"**Vector (Rules đã duyệt):** {embedded_count} / {approved_total} Rule có embedding.")
    _rules_sync_running = is_embedding_backfill_running("rules")
    if not _rules_sync_running:
        st.session_state.pop("embedding_sync_clicked_rules", None)
    if _rules_sync_running:
        st.caption("⏳ Đang đồng bộ vector (Rules). Vui lòng đợi xong rồi bấm Refresh.")
    c_vec1, c_vec2 = st.columns(2)
    with c_vec1:
        if st.button("🔄 Làm mới số liệu vector", key="rules_refresh_vec", disabled=_rules_sync_running):
            st.rerun()
    with c_vec2:
        if can_write and st.button(
            "🔄 Đồng bộ vector (Rules)",
            key="rules_sync_vec_btn",
            disabled=(need_embed == 0 or _rules_sync_running),
        ):
            import threading

            st.session_state["embedding_sync_clicked_rules"] = True

            def _run():
                run_rules_embedding_backfill(project_id, limit=200)

            threading.Thread(target=_run, daemon=True).start()
            st.toast("Đã bắt đầu đồng bộ vector cho Rules đã duyệt. Bấm **Làm mới số liệu vector** sau vài giây.")
            st.rerun()

    # Filter trạng thái duyệt: Tất cả / Đã duyệt / Chưa duyệt
    status_filter = st.selectbox(
        "Trạng thái",
        ["Tất cả", "Chỉ đã duyệt", "Chỉ chưa duyệt"],
        index=0,
        key="rules_status_filter",
        help="Lọc Rule theo trạng thái duyệt. Chỉ Rule đã duyệt mới được dùng trong context.",
    )
    # Filter phạm vi: global / project / arc (+ chọn arc)
    scope_filter = st.selectbox(
        "Phạm vi Rule",
        ["Tất cả", "Chỉ global", "Chỉ project", "Chỉ arc"],
        index=0,
        key="rules_scope_filter",
        help="Lọc Rule theo phạm vi áp dụng (global / project / arc).",
    )
    selected_arc_filter_id = None
    if scope_filter == "Chỉ arc":
        try:
            from core.arc_service import ArcService

            arcs = ArcService.list_arcs(project_id, status="active") if project_id else []
        except Exception:
            arcs = []
        arc_labels = ["(Tất cả arc)"] + [a.get("name", "") for a in arcs]
        arc_idx = st.selectbox(
            "Arc áp dụng (filter)",
            range(len(arc_labels)),
            index=0,
            format_func=lambda i: arc_labels[i] if i < len(arc_labels) else "",
            key="rules_scope_arc_filter",
        )
        if arc_idx and arc_idx < len(arc_labels) and arcs:
            selected_arc_filter_id = arcs[arc_idx - 1].get("id")

    # Filter + phân trang ở DB (tối đa 10 mục/trang)
    filter_key = (status_filter, scope_filter, str(selected_arc_filter_id or ""))
    if st.session_state.get("rules_filter_prev") != filter_key:
        st.session_state["rules_page"] = 1
    st.session_state["rules_filter_prev"] = filter_key
    page = max(1, int(st.session_state.get("rules_page", 1)))

    def _rules_query(sb, select_cols="*", with_range=None, count_exact=False):
        if count_exact:
            q = sb.table("project_rules").select("id", count="exact")
        else:
            q = sb.table("project_rules").select(select_cols)
        if scope_filter == "Chỉ global":
            q = q.eq("scope", "global")
        elif scope_filter == "Chỉ project":
            q = q.eq("scope", "project").eq("story_id", project_id)
        elif scope_filter == "Chỉ arc":
            q = q.eq("scope", "arc").eq("story_id", project_id)
            if selected_arc_filter_id:
                rule_ids_r = sb.table("project_rule_arcs").select("rule_id").eq("arc_id", selected_arc_filter_id).execute()
                rule_ids = [r["rule_id"] for r in (rule_ids_r.data or []) if r.get("rule_id")]
                if not rule_ids:
                    q = q.eq("id", "00000000-0000-0000-0000-000000000000")
                else:
                    q = q.in_("id", rule_ids)
        else:
            q = q.or_(f"scope.eq.global,and(scope.eq.project,story_id.eq.{project_id}),and(scope.eq.arc,story_id.eq.{project_id})")
        if status_filter == "Chỉ đã duyệt":
            q = q.eq("approve", True)
        elif status_filter == "Chỉ chưa duyệt":
            q = q.eq("approve", False)
        if not count_exact:
            q = q.order("created_at", desc=True)
        if count_exact:
            q = q.limit(0)
        elif with_range is not None:
            start, end = with_range
            q = q.range(start, end)
        return q.execute()

    try:
        count_res = _rules_query(supabase, count_exact=True)
        total_rules = getattr(count_res, "count", None) or 0
    except Exception:
        total_rules = 0
    total_pages = max(1, (total_rules + KNOWLEDGE_PAGE_SIZE - 1) // KNOWLEDGE_PAGE_SIZE)
    page = max(1, min(page, total_pages))
    st.session_state["rules_page"] = page
    offset = (page - 1) * KNOWLEDGE_PAGE_SIZE
    try:
        res = _rules_query(supabase, select_cols="*", with_range=(offset, offset + KNOWLEDGE_PAGE_SIZE - 1))
        rows = res.data or []
    except Exception:
        rows = []
        total_rules = 0
        total_pages = 1

    def _row_to_entry(row):
        c = row.get("content", "") or ""
        return {
            "id": row.get("id"),
            "scope": row.get("scope", "project"),
            "content": c,
            "description": c,
            "entity_name": f"[RULE] ({row.get('scope','')}) {c[:50]}",
            "created_at": row.get("created_at"),
            "approve": bool(row.get("approve", True)),
            "source": "project_rules",
            "type": row.get("type", "Unknown"),
        }

    rules_data = [_row_to_entry(r) for r in rows]
    st.metric("Tổng Rules", total_rules)

    if st.button("➕ Thêm Rule mới", key="rules_add") and can_write:
        st.session_state["rules_adding"] = True

    if st.session_state.get("rules_adding") and can_write:
        st.markdown("---")
        with st.form("add_rule_form"):
            rule_content = st.text_area("Nội dung Rule", height=100, key="new_rule_content")
            rule_scope = st.selectbox("Phạm vi", ["project", "global", "arc"], index=0, key="new_rule_scope")
            rule_type = st.selectbox(
                "Loại Rule",
                ["Unknown", "Style", "Method", "Info"],
                index=0,
                key="new_rule_type",
                help="Style: phong cách / thoại; Method: cách lấy dữ liệu / intent; Info: dữ liệu/tri thức; Unknown: chưa phân loại.",
            )

            # Multi-arc khi thêm mới nếu scope=arc
            from core.arc_service import ArcService

            selected_arc_ids_new = []
            arcs = []
            if rule_scope == "arc":
                try:
                    arcs = ArcService.list_arcs(project_id, status="active") if project_id else []
                except Exception:
                    arcs = []
                new_arc_objs = st.multiselect(
                    "Arc áp dụng (có thể chọn nhiều)",
                    arcs,
                    format_func=lambda a: (a.get("name") or "").strip() or "Arc" if isinstance(a, dict) else str(a),
                    key="new_rule_arcs",
                )
                selected_arc_ids_new = [a.get("id") for a in new_arc_objs if a.get("id")]

            if st.form_submit_button("💾 Lưu"):
                if rule_content and rule_content.strip():
                    try:
                        payload = {"scope": rule_scope, "content": rule_content.strip(), "type": rule_type}
                        if rule_scope == "global":
                            pass  # story_id, arc_id NULL
                        elif rule_scope == "project":
                            payload["story_id"] = project_id
                        else:
                            payload["story_id"] = project_id
                            # Lưu arc_id legacy = arc đầu tiên (nếu có) để tương thích
                            payload["arc_id"] = selected_arc_ids_new[0] if selected_arc_ids_new else None
                        res = supabase.table("project_rules").insert(payload).execute()
                        # Thêm map project_rule_arcs nếu scope=arc và có nhiều arc
                        try:
                            if rule_scope == "arc" and selected_arc_ids_new:
                                new_rows = list(res.data or [])
                                if new_rows:
                                    rule_id = new_rows[0].get("id")
                                    if rule_id:
                                        map_payloads = [{"rule_id": rule_id, "arc_id": aid} for aid in selected_arc_ids_new]
                                        supabase.table("project_rule_arcs").insert(map_payloads).execute()
                        except Exception:
                            pass
                        st.success("Đã thêm Rule (cấp %s, loại %s)." % (rule_scope, rule_type))
                        st.session_state["rules_adding"] = False
                        invalidate_cache()
                        st.rerun()
                    except Exception as e:
                        st.error(str(e))
            if st.form_submit_button("Hủy"):
                st.session_state["rules_adding"] = False

    st.markdown("---")
    if total_pages > 1:
        pcol1, pcol2, pcol3 = st.columns([1, 2, 1])
        with pcol1:
            if st.button("⬅️ Trang trước", key="rules_prev_page", disabled=(page <= 1)):
                st.session_state["rules_page"] = max(1, page - 1)
                st.rerun()
        with pcol2:
            st.caption(f"**Trang {page} / {total_pages}** (tối đa {KNOWLEDGE_PAGE_SIZE} mục/trang)")
        with pcol3:
            if st.button("Trang sau ➡️", key="rules_next_page", disabled=(page >= total_pages)):
                st.session_state["rules_page"] = min(total_pages, page + 1)
                st.rerun()

    if not rules_data and total_rules == 0:
        st.info("Chưa có Rule nào.")
        return

    for entry in rules_data:
        approved = entry.get("approve", True)
        approve_badge = " ✅ ĐÃ DUYỆT" if approved else " ⏳ CHƯA DUYỆT"
        r_type = entry.get("type") or "Unknown"
        type_badge = f" 🔧 {r_type.upper()}"
        with st.expander(f"**{entry.get('entity_name','')[:60]}** [{entry.get('scope','project')}] {approve_badge}{type_badge}", expanded=False):
            st.markdown(entry.get("description", "") or entry.get("content", ""))
            col1, col2, col3 = st.columns(3)
            with col1:
                if st.button("✏️ Sửa", key=f"rule_edit_{entry['id']}") and can_write:
                    st.session_state["rules_editing"] = entry
            with col2:
                if can_delete and st.button("🗑️ Xóa", key=f"rule_del_{entry['id']}"):
                    try:
                        if entry.get("source") == "project_rules":
                            supabase.table("project_rules").delete().eq("id", entry["id"]).execute()
                        else:
                            supabase.table("story_bible").delete().eq("id", entry["id"]).execute()
                        st.success("Đã xóa.")
                        invalidate_cache()
                        st.rerun()
                    except Exception as e:
                        st.error(str(e))
            with col3:
                if entry.get("source") == "project_rules" and can_write:
                    if not approved and st.button("✅ Approve", key=f"rule_approve_{entry['id']}"):
                        try:
                            from datetime import datetime
                            supabase.table("project_rules").update({"approve": True, "updated_at": datetime.utcnow().isoformat()}).eq("id", entry["id"]).execute()
                            st.success("Đã duyệt Rule.")
                            invalidate_cache()
                            st.rerun()
                        except Exception as e:
                            st.error(str(e))

    if st.session_state.get("rules_editing") and can_write:
        e = st.session_state["rules_editing"]
        st.markdown("---")
        with st.form("edit_rule_form"):
            new_desc = st.text_area("Nội dung", value=e.get("description", "") or e.get("content", ""), height=100)
            cur_type = e.get("type") or "Unknown"
            type_options = ["Unknown", "Style", "Method", "Info"]
            try:
                idx_type = type_options.index(cur_type) if cur_type in type_options else 0
            except ValueError:
                idx_type = 0
            new_type = st.selectbox(
                "Loại Rule",
                type_options,
                index=idx_type,
                key=f"edit_rule_type_{e['id']}",
                help="Style: phong cách/thoại; Method: cách lấy dữ liệu/intent; Info: dữ liệu/tri thức; Unknown: chưa phân loại.",
            )

            # Cho phép sửa phạm vi: global / project / arc
            cur_scope = e.get("scope") or "project"
            scope_options = ["global", "project", "arc"]
            try:
                idx_scope = scope_options.index(cur_scope) if cur_scope in scope_options else 1
            except ValueError:
                idx_scope = 1
            new_scope = st.selectbox(
                "Phạm vi Rule",
                scope_options,
                index=idx_scope,
                key=f"edit_rule_scope_{e['id']}",
                help="global: áp dụng mọi project; project: chỉ project hiện tại; arc: chỉ các arc được chọn.",
            )

            # Multi-arc cho scope=arc
            from core.arc_service import ArcService

            arc_ids_existing = e.get("arc_ids") or []
            if not arc_ids_existing and e.get("arc_id"):
                arc_ids_existing = [str(e.get("arc_id"))]
            selected_arc_ids = []
            if new_scope == "arc":
                try:
                    arcs = ArcService.list_arcs(project_id, status="active") if project_id else []
                except Exception:
                    arcs = []
                arc_name_by_id = {str(a.get("id")): (a.get("name") or "").strip() or "Arc" for a in arcs}
                default_arc_objs = [a for a in arcs if str(a.get("id")) in set(map(str, arc_ids_existing))]
                new_arc_objs = st.multiselect(
                    "Arc áp dụng (có thể chọn nhiều)",
                    arcs,
                    default=default_arc_objs,
                    format_func=lambda a: (a.get("name") or "").strip() or "Arc" if isinstance(a, dict) else str(a),
                    key=f"edit_rule_arcs_{e['id']}",
                    help="Một Rule có thể gắn cho nhiều Arc.",
                )
                selected_arc_ids = [a.get("id") for a in new_arc_objs if a.get("id")]

            if st.form_submit_button("💾 Cập nhật"):
                try:
                    if e.get("source") == "project_rules":
                        from datetime import datetime
                        update_payload = {
                            "content": new_desc,
                            "updated_at": datetime.utcnow().isoformat(),
                            "embedding": None,
                            "type": new_type,
                            "scope": new_scope,
                        }
                        # Cập nhật story_id / arc_id legacy theo scope
                        if new_scope == "global":
                            update_payload["story_id"] = None
                            update_payload["arc_id"] = None
                        elif new_scope == "project":
                            update_payload["story_id"] = project_id
                            update_payload["arc_id"] = None
                        else:
                            update_payload["story_id"] = project_id
                            # Lưu arc_id legacy = arc đầu tiên (nếu có) để tương thích ngược
                            update_payload["arc_id"] = selected_arc_ids[0] if selected_arc_ids else None

                        supabase.table("project_rules").update(update_payload).eq("id", e["id"]).execute()

                        # Quản lý bảng map project_rule_arcs cho scope=arc
                        if new_scope != "arc":
                            try:
                                supabase.table("project_rule_arcs").delete().eq("rule_id", e["id"]).execute()
                            except Exception:
                                pass
                        else:
                            try:
                                # Xóa map cũ
                                supabase.table("project_rule_arcs").delete().eq("rule_id", e["id"]).execute()
                                # Thêm map mới theo selected_arc_ids
                                if selected_arc_ids:
                                    payloads = [{"rule_id": e["id"], "arc_id": aid} for aid in selected_arc_ids]
                                    supabase.table("project_rule_arcs").insert(payloads).execute()
                            except Exception:
                                pass
                    else:
                        # Legacy story_bible rule chỉ cập nhật nội dung + reset embedding
                        supabase.table("story_bible").update({"description": new_desc, "embedding": None}).eq("id", e["id"]).execute()
                    st.success("Đã cập nhật.")
                    del st.session_state["rules_editing"]
                    invalidate_cache()
                    st.rerun()
                except Exception as ex:
                    st.error(str(ex))
            if st.form_submit_button("Hủy"):
                del st.session_state["rules_editing"]

    st.markdown("---")
    with st.expander("💀 Danger Zone", expanded=False):
        st.markdown('<div class="danger-zone">', unsafe_allow_html=True)
        if can_delete:
            confirm = st.checkbox("Xóa sạch TẤT CẢ Rules (chỉ project hiện tại)", key="rules_confirm_clear")
            if confirm and st.button("🗑️ Xóa sạch Rules"):
                legacy_ids = [r["id"] for r in rules_data if r.get("source") == "story_bible"]
                new_ids = [r["id"] for r in rules_data if r.get("source") == "project_rules" and r.get("scope") == "project"]
                if legacy_ids:
                    supabase.table("story_bible").delete().in_("id", legacy_ids).execute()
                if new_ids:
                    supabase.table("project_rules").delete().in_("id", new_ids).execute()
                if legacy_ids or new_ids:
                    st.success("Đã xóa sạch Rules.")
                    invalidate_cache()
                    st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)
