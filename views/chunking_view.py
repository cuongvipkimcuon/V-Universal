# views/chunking_view.py - Danh sách chunks đã lưu: xem, sửa nội dung, vector lại, xóa
"""Chunking: chỉ quản lý chunks đã lưu. Logic tách chunk (Workstation) nằm trong utils.chunk_tools."""
import streamlit as st

from config import init_services
from utils.auth_manager import check_permission

KNOWLEDGE_PAGE_SIZE = 10


def _ensure_chunks_table(supabase):
    """Đảm bảo bảng chunks tồn tại (schema v6)."""
    try:
        supabase.table("chunks").select("id").limit(1).execute()
        return True
    except Exception:
        return False


def render_chunking_tab(project_id):
    """Tab Chunking - Chỉ hiển thị và quản lý chunks đã lưu: sửa nội dung, vector lại, xóa."""
    st.subheader("✂️ Chunks đã lưu")
    st.caption("Chunks được vector hóa để search trong Chat. Bấm **Refresh** để tải lại. Sửa nội dung rồi bấm **Cập nhật & Vector lại** để không phải chunk lại từ đầu.")

    if not project_id:
        st.info("📁 Chọn Project trước.")
        return

    services = init_services()
    if not services:
        st.warning("Không kết nối được dịch vụ.")
        return
    supabase = services["supabase"]

    if not _ensure_chunks_table(supabase):
        st.warning("Bảng chunks chưa tồn tại. Chạy schema_v6_migration.sql trong Supabase.")
        return

    # Số chunk chưa có embedding — luôn hiển thị lên đầu
    try:
        null_emb = supabase.table("chunks").select("id").eq("story_id", project_id).is_("embedding", "NULL").limit(1001).execute()
        chunks_no_vec = len(null_emb.data or [])
        if chunks_no_vec > 1000:
            chunks_no_vec = 1001
    except Exception:
        chunks_no_vec = 0
    lbl = "1000+" if chunks_no_vec > 1000 else str(chunks_no_vec)
    st.caption(f"**Vector:** {lbl} chunk chưa có embedding.")

    user = st.session_state.get("user")
    user_id = getattr(user, "id", None) if user else None
    user_email = getattr(user, "email", None) if user else None
    can_write = bool(
        project_id and user_id
        and check_permission(str(user_id), user_email or "", project_id, "write")
    )
    can_delete = check_permission(str(user_id or ""), user_email or "", project_id, "delete")

    if st.button("🔄 Refresh", key="chunking_refresh_btn"):
        st.rerun()
    from core.background_jobs import is_embedding_backfill_running
    _chunks_running = is_embedding_backfill_running("chunks")
    if not _chunks_running and st.session_state.get("embedding_sync_clicked_chunks"):
        st.session_state.pop("embedding_sync_clicked_chunks", None)
    sync_chunks = _chunks_running or st.session_state.get("embedding_sync_clicked_chunks", False)
    if sync_chunks:
        st.caption("⏳ Đang đồng bộ vector (Chunks). Vui lòng đợi xong rồi bấm Refresh.")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("🔄 Kiểm tra chunk chưa có embedding", key="chunking_check_vec_btn", disabled=sync_chunks):
            st.rerun()
    with c2:
        if st.button("🔄 Đồng bộ vector (Chunks)", key="chunking_sync_vec_btn", disabled=(chunks_no_vec == 0 or sync_chunks)):
            import threading
            from core.background_jobs import run_embedding_backfill
            st.session_state["embedding_sync_clicked_chunks"] = True
            def _run():
                run_embedding_backfill(project_id, bible_limit=0, chunks_limit=200)
            threading.Thread(target=_run, daemon=True).start()
            st.toast("Đã bắt đầu đồng bộ vector (Chunks). Bấm Refresh sau vài giây để xem kết quả.")
            st.rerun()

    # Filter theo chương (chapter_id)
    try:
        ch_list = supabase.table("chapters").select("id, chapter_number, title").eq("story_id", project_id).order("chapter_number").execute().data or []
        ck_chapter_options = ["Tất cả"] + [f"Chương {r.get('chapter_number', '')}: {r.get('title') or ''}" for r in ch_list]
        ck_chapter_ids = [None] + [r.get("id") for r in ch_list]
    except Exception:
        ck_chapter_options = ["Tất cả"]
        ck_chapter_ids = [None]
    ck_filter_chapter_idx = st.session_state.get("chunking_filter_chapter", 0)
    ck_filter_chapter_idx = max(0, min(ck_filter_chapter_idx, len(ck_chapter_options) - 1))
    ck_filter_chapter_label = st.selectbox(
        "Chương",
        range(len(ck_chapter_options)),
        index=ck_filter_chapter_idx,
        format_func=lambda i: ck_chapter_options[i] if i < len(ck_chapter_options) else "",
        key="chunking_filter_chapter_select",
        help="Chỉ hiển thị chunk thuộc chương đã chọn.",
    )
    st.session_state["chunking_filter_chapter"] = ck_filter_chapter_label
    ck_filter_chapter_id = ck_chapter_ids[ck_filter_chapter_label] if ck_filter_chapter_label < len(ck_chapter_ids) else None
    if ck_filter_chapter_id is not None and st.session_state.get("chunking_filter_chapter_prev") != ck_filter_chapter_label:
        st.session_state["chunking_page"] = 1
    st.session_state["chunking_filter_chapter_prev"] = ck_filter_chapter_label

    # Phân trang ở DB (tối đa 10 mục/trang)
    page = max(1, int(st.session_state.get("chunking_page", 1)))
    try:
        count_q = supabase.table("chunks").select("id", count="exact").eq("story_id", project_id)
        if ck_filter_chapter_id is not None:
            count_q = count_q.eq("chapter_id", ck_filter_chapter_id)
        count_res = count_q.limit(0).execute()
        total_chunks = getattr(count_res, "count", None) or 0
    except Exception:
        total_chunks = 0
    total_pages = max(1, (total_chunks + KNOWLEDGE_PAGE_SIZE - 1) // KNOWLEDGE_PAGE_SIZE)
    page = max(1, min(page, total_pages))
    st.session_state["chunking_page"] = page
    offset = (page - 1) * KNOWLEDGE_PAGE_SIZE
    r_q = (
        supabase.table("chunks")
        .select("id, content, raw_content, source_type, meta_json, arc_id, chapter_id, sort_order")
        .eq("story_id", project_id)
        .order("sort_order")
    )
    if ck_filter_chapter_id is not None:
        r_q = r_q.eq("chapter_id", ck_filter_chapter_id)
    r = r_q.range(offset, offset + KNOWLEDGE_PAGE_SIZE - 1).execute()
    chunks_list = r.data or []
    try:
        if chunks_list:
            chunk_ids = [c.get("id") for c in chunks_list if c.get("id")]
            if chunk_ids:
                null_emb = supabase.table("chunks").select("id").in_("id", chunk_ids).is_("embedding", "NULL").execute()
                ids_no_embedding = {row["id"] for row in (null_emb.data or []) if row.get("id")}
            else:
                ids_no_embedding = set()
        else:
            ids_no_embedding = set()
    except Exception:
        ids_no_embedding = set()
    st.metric("Tổng chunks", total_chunks)
    if total_pages > 1:
        pcol1, pcol2, pcol3 = st.columns([1, 2, 1])
        with pcol1:
            if st.button("⬅️ Trang trước", key="chunk_prev_page", disabled=(page <= 1)):
                st.session_state["chunking_page"] = max(1, page - 1)
                st.rerun()
        with pcol2:
            st.caption(f"**Trang {page} / {total_pages}** (tối đa {KNOWLEDGE_PAGE_SIZE} mục/trang)")
        with pcol3:
            if st.button("Trang sau ➡️", key="chunk_next_page", disabled=(page >= total_pages)):
                st.session_state["chunking_page"] = min(total_pages, page + 1)
                st.rerun()
    for c in chunks_list:
            cid = c.get("id")
            content = (c.get("content") or c.get("raw_content") or "").strip()
            meta = c.get("meta_json") or {}
            sm = meta.get("source_metadata", meta) if isinstance(meta, dict) else meta
            label = (
                sm.get("sheet_name", "")
                or sm.get("source_file", "")
                or (meta.get("title") if isinstance(meta, dict) else "")
                or c.get("source_type", "")
                or str(cid or "")[:8]
            )
            short = (content[:60] + "…") if len(content) > 60 else content
            sync_badge = " 🔄 Chưa đồng bộ" if cid in ids_no_embedding else ""

            with st.expander(f"Chunk: {label} — {short}{sync_badge}", expanded=False):
                if cid in ids_no_embedding:
                    st.caption("🔄 Chưa đồng bộ vector — bấm **Đồng bộ vector (Chunks)** trên để cập nhật.")
                st.text(content[:500] + ("…" if len(content) > 500 else ""))

                if can_write:
                    edit_key = f"chunk_edit_{cid}"
                    update_key = f"chunk_update_vec_{cid}"
                    new_content = st.text_area(
                        "Sửa nội dung (sau đó bấm Cập nhật & Vector lại)",
                        value=content,
                        height=120,
                        key=edit_key,
                    )
                    if st.button("🔄 Cập nhật nội dung", key=update_key, type="primary"):
                        if not (new_content and new_content.strip()):
                            st.warning("Nội dung không được để trống.")
                        else:
                            try:
                                supabase.table("chunks").update({
                                    "content": new_content.strip(),
                                    "raw_content": new_content.strip(),
                                    "embedding": None,
                                }).eq("id", cid).execute()
                                st.success("Đã cập nhật. Bấm **Đồng bộ vector (Chunks)** trên để cập nhật embedding.")
                            except Exception as e:
                                st.error(str(e))

                if can_delete and st.button("🗑️ Xóa", key=f"chunk_del_{cid}"):
                    supabase.table("chunks").delete().eq("id", cid).execute()
                    st.success("Đã xóa.")

    st.markdown("---")
    with st.expander("💀 Danger Zone", expanded=False):
        st.markdown('<div class="danger-zone">', unsafe_allow_html=True)
        if can_delete and total_chunks:
            confirm = st.checkbox("Xóa sạch TẤT CẢ chunks", key="chunk_confirm_clear")
            if confirm and st.button("🗑️ Xóa sạch Chunks"):
                supabase.table("chunks").delete().eq("story_id", project_id).execute()
                st.success("Đã xóa sạch.")
        st.markdown("</div>", unsafe_allow_html=True)
