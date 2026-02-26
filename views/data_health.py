# views/data_health.py - V6 MODULE 5 + V7.7 Data Health (Active Sentry + Lỗi logic theo chương)
"""
- Validation conflicts (validation_logs): Force Sync | Keep Exception.
- Lỗi logic theo chương: chọn chương -> Soát chương (5 dimensions); hiển thị active + đã khắc phục.
"""
import streamlit as st

from config import init_services
from utils.active_sentry import resolve_conflict
from utils.cache_helpers import get_chapters_cached
from core.chapter_logic_check import run_chapter_logic_check, get_chapter_logic_issues, LOGIC_DIMENSIONS

KNOWLEDGE_PAGE_SIZE = 10


def render_data_health_tab(project_id):
    """Tab Data Health: validation_logs + chapter logic issues (soát từng chương, đã khắc phục)."""
    st.subheader("🛡️ Data Health")

    if not project_id:
        st.info("📁 Chọn Project ở thanh bên trái.")
        return

    # ---------- Phần 1: Validation conflicts (Active Sentry) — phân trang ở DB ----------
    st.markdown("#### ⚠️ Conflicts (Bible / Cross-sheet)")
    services = init_services()
    conflicts = []
    total_conflicts = 0
    conflicts_page = 1
    conflicts_total_pages = 1
    if services:
        try:
            sb = services["supabase"]
            count_res = sb.table("validation_logs").select("id", count="exact").eq("story_id", project_id).eq("status", "pending").limit(0).execute()
            total_conflicts = getattr(count_res, "count", None) or 0
            conflicts_total_pages = max(1, (total_conflicts + KNOWLEDGE_PAGE_SIZE - 1) // KNOWLEDGE_PAGE_SIZE)
            conflicts_page = max(1, min(int(st.session_state.get("data_health_conflicts_page", 1)), conflicts_total_pages))
            st.session_state["data_health_conflicts_page"] = conflicts_page
            offset = (conflicts_page - 1) * KNOWLEDGE_PAGE_SIZE
            r = sb.table("validation_logs").select("*").eq("story_id", project_id).eq("status", "pending").order("created_at", desc=True).range(offset, offset + KNOWLEDGE_PAGE_SIZE - 1).execute()
            conflicts = r.data or []
        except Exception:
            pass
    if not conflicts and total_conflicts == 0:
        st.success("✅ Không có xung đột đang chờ.")
    else:
        if conflicts_total_pages > 1:
            st.caption(f"**Trang {conflicts_page} / {conflicts_total_pages}** (tối đa {KNOWLEDGE_PAGE_SIZE} mục/trang, tổng {total_conflicts} xung đột)")
            dc1, dc2, dc3 = st.columns([1, 2, 1])
            with dc1:
                if st.button("⬅️ Trang trước", key="dh_conf_prev", disabled=(conflicts_page <= 1)):
                    st.session_state["data_health_conflicts_page"] = max(1, conflicts_page - 1)
                    st.rerun()
            with dc3:
                if st.button("Trang sau ➡️", key="dh_conf_next", disabled=(conflicts_page >= conflicts_total_pages)):
                    st.session_state["data_health_conflicts_page"] = min(conflicts_total_pages, conflicts_page + 1)
                    st.rerun()
        for c in conflicts:
            log_id = c.get("id")
            msg = c.get("message", "")
            log_type = c.get("log_type", "other")
            details = c.get("details") or {}
            with st.expander("⚠️ %s — %s" % (log_type, msg[:80])):
                st.write("**Message:** %s" % msg)
                if details:
                    st.json(details)
                col1, col2 = st.columns(2)
                with col1:
                    if st.button("✅ Force Sync with Bible", key="force_%s" % log_id):
                        if resolve_conflict(log_id, "resolved_force_sync", resolved_by=getattr(st.session_state.get("user"), "email", "")):
                            st.toast("Đã đánh dấu: Force Sync with Bible. Bấm Refresh để cập nhật.")
                with col2:
                    if st.button("📌 Keep Exception", key="keep_%s" % log_id):
                        if resolve_conflict(log_id, "resolved_keep_exception", resolved_by=getattr(st.session_state.get("user"), "email", "")):
                            st.toast("Đã đánh dấu: Keep Exception. Bấm Refresh để cập nhật.")

    st.markdown("---")
    st.markdown("#### 📋 Lỗi logic theo chương (Timeline, Bible, Relation, Chat crystallize, Rule)")

    file_list = get_chapters_cached(project_id, st.session_state.get("update_trigger", 0))
    if not file_list:
        st.info("Chưa có chương nào. Tạo chương trong Workstation trước khi soát.")
        return

    chapter_options = {}
    for f in file_list:
        ch_id = f.get("id")
        ch_num = f.get("chapter_number")
        title = f.get("title") or ("Chương %s" % ch_num)
        if ch_id is not None:
            chapter_options["#%s: %s" % (ch_num, title)] = (ch_id, ch_num, title, f.get("content") or "", f.get("arc_id"))

    selected_label = st.selectbox(
        "Chọn chương để soát",
        options=list(chapter_options.keys()),
        key="data_health_chapter_select",
    )
    if not selected_label:
        return
    ch_id, ch_num, ch_title, ch_content, arc_id = chapter_options[selected_label]

    # Soát theo dimension: All hoặc chọn từng mục (giảm prompt và token)
    st.caption("Soát theo dimension (chọn All hoặc từng mục để giảm prompt/token):")
    dim_options = ["All"] + list(LOGIC_DIMENSIONS)
    selected_dims = st.multiselect(
        "Dimensions cần soát",
        options=dim_options,
        default=["All"],
        key="data_health_dimensions",
        help="All = soát cả 5; hoặc chọn Timeline, Bible, Relation, Chat crystallize, Rule để chỉ soát từng loại.",
    )
    if "All" in selected_dims or not selected_dims:
        logic_dimensions = None  # full
    else:
        logic_dimensions = [d for d in selected_dims if d in LOGIC_DIMENSIONS]

    if st.button("🔍 Soát chương này", type="primary", key="data_health_scan_btn", width="stretch"):
        spinner_msg = "Đang soát logic (5 dimensions)..." if logic_dimensions is None else "Đang soát logic (%s)..." % ", ".join(logic_dimensions)
        with st.spinner(spinner_msg):
            issues, resolved_count, check_id, err = run_chapter_logic_check(
                project_id, ch_id, ch_num, ch_title, ch_content, arc_id=arc_id, dimensions=logic_dimensions
            )
            if err:
                st.error("Lỗi: %s" % err)
            else:
                st.success("Phát hiện %s lỗi; đã đánh dấu khắc phục %s lỗi cũ. Bấm Refresh để tải lại danh sách." % (len(issues), resolved_count))

    # Danh sách issues: active + resolved (đã khắc phục) — phân trang ở DB (10 mục/trang)
    issues_page = max(1, int(st.session_state.get("data_health_issues_page", 1)))
    total_issues = 0
    issues_total_pages = 1
    if services:
        try:
            count_res = services["supabase"].table("chapter_logic_issues").select("id", count="exact").eq("story_id", project_id).eq("chapter_id", ch_id).limit(0).execute()
            total_issues = getattr(count_res, "count", None) or 0
            issues_total_pages = max(1, (total_issues + KNOWLEDGE_PAGE_SIZE - 1) // KNOWLEDGE_PAGE_SIZE)
            issues_page = max(1, min(issues_page, issues_total_pages))
            st.session_state["data_health_issues_page"] = issues_page
        except Exception:
            pass
    all_issues = get_chapter_logic_issues(project_id, chapter_id=ch_id, status_filter=None, limit=KNOWLEDGE_PAGE_SIZE, offset=(issues_page - 1) * KNOWLEDGE_PAGE_SIZE)
    active = [i for i in all_issues if i.get("status") == "active"]
    resolved = [i for i in all_issues if i.get("status") == "resolved"]

    if issues_total_pages > 1:
        st.caption(f"**Trang {issues_page} / {issues_total_pages}** (tối đa {KNOWLEDGE_PAGE_SIZE} lỗi/trang, tổng {total_issues})")
        di1, di2, di3 = st.columns([1, 2, 1])
        with di1:
            if st.button("⬅️ Trang trước", key="dh_iss_prev", disabled=(issues_page <= 1)):
                st.session_state["data_health_issues_page"] = max(1, issues_page - 1)
                st.rerun()
        with di3:
            if st.button("Trang sau ➡️", key="dh_iss_next", disabled=(issues_page >= issues_total_pages)):
                st.session_state["data_health_issues_page"] = min(issues_total_pages, issues_page + 1)
                st.rerun()

    if active:
        st.markdown("**Lỗi đang có (cần sửa)**")
        for i in active:
            dim = i.get("dimension", "")
            msg = i.get("message", "")
            with st.expander("🔴 %s — %s" % (dim, (msg[:60] + "…") if len(msg) > 60 else msg)):
                st.write(msg)
                if i.get("details"):
                    st.json(i["details"])
    if resolved:
        st.markdown("**Đã khắc phục** (lần soát trước không còn lỗi này)")
        for i in resolved:
            dim = i.get("dimension", "")
            msg = i.get("message", "")
            resolved_at = i.get("resolved_at", "")
            st.caption("✅ %s — %s (khắc phục: %s)" % (dim, (msg[:50] + "…") if len(msg) > 50 else msg, resolved_at[:10] if resolved_at else "—"))
    if not active and not resolved:
        st.caption("Chưa có bản ghi lỗi cho chương này. Bấm **Soát chương này** để chạy kiểm tra.")
