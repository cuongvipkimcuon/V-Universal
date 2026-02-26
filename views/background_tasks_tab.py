# views/background_tasks_tab.py - Background Jobs tab: list jobs, nút Refresh, nút Thử lại khi thất bại
import streamlit as st

from config import init_services
from core.background_jobs import list_jobs, retry_job_with_stored_data, ensure_background_job_runner
from core.job_llm_store import has_stored_result_for_retry


def render_background_tasks_tab(project_id):
    if not project_id:
        st.info("Please select a project in the sidebar.")
        return

    st.subheader("Background Jobs")
    st.caption("Data Analyze and batch operations. Bấm **Refresh** để tải lại. Job thất bại có dữ liệu lưu sẽ có nút **Thử lại** (không gọi LLM).")

    try:
        services = init_services()
        if not services:
            st.warning("Could not connect to services.")
            return
    except Exception:
        st.warning("Could not connect to services.")
        return

    col_ctrl1, col_ctrl2 = st.columns(2)
    with col_ctrl1:
        if st.button("🔄 Refresh", key="bg_tasks_refresh_btn"):
            st.rerun()
    with col_ctrl2:
        if st.button("▶️ Chạy hàng đợi", key="bg_tasks_run_queue_btn"):
            ensure_background_job_runner()
            st.success("Đã kích hoạt xử lý hàng đợi. Các job pending (chưa quá hạn) sẽ được chạy lần lượt trong nền.")

    status_filter = st.selectbox(
        "Status",
        ["All", "pending", "running", "completed", "failed"],
        key="bg_tasks_filter",
    )
    status_key = None if status_filter == "All" else status_filter
    jobs = list_jobs(project_id, status_filter=status_key, limit=80)
    if not jobs:
        st.info("No background jobs yet.")
        return

    for j in jobs:
        status = j.get("status", "pending")
        label = j.get("label", "Job")
        job_id = j.get("id")
        job_type = j.get("job_type", "")
        created = j.get("created_at") or ""
        started = j.get("started_at") or ""
        completed = j.get("completed_at") or ""
        result_summary = j.get("result_summary") or ""
        error_message = j.get("error_message") or ""

        if isinstance(created, str) and len(created) > 19:
            created = created[:19].replace("T", " ")
        if isinstance(started, str) and len(started) > 19:
            started = started[:19].replace("T", " ")
        if isinstance(completed, str) and len(completed) > 19:
            completed = completed[:19].replace("T", " ")

        if status == "running":
            icon = "🔄"
        elif status == "completed":
            icon = "✅"
        elif status == "failed":
            icon = "❌"
        else:
            icon = "⏸️"

        with st.expander(f"{icon} **{label}** — {status}", expanded=(status in ("running", "failed"))):
            st.caption(f"Type: {job_type} | Created: {created}")
            if started:
                st.caption(f"Started: {started}")
            if completed:
                st.caption(f"Completed: {completed}")
            if result_summary:
                st.success(result_summary)
            if error_message:
                st.error(error_message)
            if status == "failed" and job_id and has_stored_result_for_retry(job_id):
                st.caption("Có dữ liệu LLM đã lưu. Bạn có thể thử lại (không gọi LLM) hoặc tạo job mới.")
                if st.button("🔄 Thử lại", key=f"bg_retry_{job_id}"):
                    with st.spinner("Đang thử lại..."):
                        out = retry_job_with_stored_data(job_id)
                    if out.get("success"):
                        st.success("Thử lại thành công.")
                        st.rerun()
                    else:
                        if out.get("retry_still_failed"):
                            st.error("Thử lại vẫn thất bại. Nên làm lại từ đầu (tạo job mới).")
                        else:
                            st.error(out.get("error") or "Lỗi khi thử lại.")
            elif status == "failed" and job_id and not has_stored_result_for_retry(job_id):
                st.warning("Không có dữ liệu đã lưu để thử lại. Nên làm lại từ đầu (tạo job mới).")
