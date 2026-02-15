# views/data_health.py - V6 MODULE 5 + V7.7 Data Health (Active Sentry + Lỗi logic theo chương)
"""
- Validation conflicts (validation_logs): Force Sync | Keep Exception.
- Lỗi logic theo chương: chọn chương -> Soát chương (5 dimensions); hiển thị active + đã khắc phục.
"""
import streamlit as st

from utils.active_sentry import get_pending_conflicts, resolve_conflict
from utils.cache_helpers import get_chapters_cached
from core.chapter_logic_check import run_chapter_logic_check, get_chapter_logic_issues


def render_data_health_tab(project_id):
    """Tab Data Health: validation_logs + chapter logic issues (soát từng chương, đã khắc phục)."""
    st.subheader("🛡️ Data Health")

    if not project_id:
        st.info("📁 Chọn Project ở thanh bên trái.")
        return

    # ---------- Phần 1: Validation conflicts (Active Sentry) ----------
    st.markdown("#### ⚠️ Conflicts (Bible / Cross-sheet)")
    conflicts = get_pending_conflicts(project_id)
    if not conflicts:
        st.success("✅ Không có xung đột đang chờ.")
    else:
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
                            st.toast("Đã đánh dấu: Force Sync with Bible.")
                            st.rerun()
                with col2:
                    if st.button("📌 Keep Exception", key="keep_%s" % log_id):
                        if resolve_conflict(log_id, "resolved_keep_exception", resolved_by=getattr(st.session_state.get("user"), "email", "")):
                            st.toast("Đã đánh dấu: Keep Exception.")
                            st.rerun()

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

    if st.button("🔍 Soát chương này", type="primary", key="data_health_scan_btn", use_container_width=True):
        with st.spinner("Đang soát logic (5 dimensions)..."):
            issues, resolved_count, check_id, err = run_chapter_logic_check(
                project_id, ch_id, ch_num, ch_title, ch_content, arc_id=arc_id
            )
            if err:
                st.error("Lỗi: %s" % err)
            else:
                st.success("Phát hiện %s lỗi; đã đánh dấu khắc phục %s lỗi cũ. Xem bên dưới." % (len(issues), resolved_count))
                st.rerun()

    # Danh sách issues: active + resolved (đã khắc phục)
    all_issues = get_chapter_logic_issues(project_id, chapter_id=ch_id, status_filter=None, limit=100)
    active = [i for i in all_issues if i.get("status") == "active"]
    resolved = [i for i in all_issues if i.get("status") == "resolved"]

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
