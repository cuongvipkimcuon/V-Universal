# views/background_tasks_tab.py - Tab Tác vụ ngầm: danh sách job đang chạy & đã xong
from datetime import datetime

import streamlit as st

from config import init_services
from core.background_jobs import list_jobs


def render_background_tasks_tab(project_id):
    if not project_id:
        st.info("📁 Vui lòng chọn Project ở thanh bên trái.")
        return

    st.subheader("⏳ Tác vụ ngầm")
    st.caption("Các tác vụ từ Data Analyze hoặc Chat (data analyze) đang chạy hoặc đã hoàn thành. V Work vẫn nhận thông báo khi xong.")

    try:
        services = init_services()
        if not services:
            st.warning("Không kết nối được dịch vụ.")
            return
    except Exception:
        st.warning("Không kết nối được dịch vụ.")
        return

    status_filter = st.selectbox(
        "Lọc trạng thái",
        ["Tất cả", "pending", "running", "completed", "failed"],
        key="bg_tasks_filter",
    )
    status_key = None if status_filter == "Tất cả" else status_filter
    jobs = list_jobs(project_id, status_filter=status_key, limit=80)
    if not jobs:
        st.info("Chưa có tác vụ ngầm nào.")
        return

    for j in jobs:
        status = j.get("status", "pending")
        label = j.get("label", "Tác vụ")
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
            color = "primary"
        elif status == "completed":
            icon = "✅"
            color = "green"
        elif status == "failed":
            icon = "❌"
            color = "red"
        else:
            icon = "⏸️"
            color = "gray"

        with st.expander(f"{icon} **{label}** — {status}", expanded=(status in ("running", "failed"))):
            st.caption(f"Loại: {job_type} | Tạo lúc: {created}")
            if started:
                st.caption(f"Bắt đầu: {started}")
            if completed:
                st.caption(f"Kết thúc: {completed}")
            if result_summary:
                st.success(result_summary)
            if error_message:
                st.error(error_message)
