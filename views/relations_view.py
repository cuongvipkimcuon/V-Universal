# views/relations_view.py - Quản lý quan hệ giữa các entity trong Bible
"""Tab Relations: danh sách quan hệ dạng thẻ (như Bible), chỉnh sửa từng mục, không hiện ID."""
import errno

import streamlit as st

from config import init_services
from utils.auth_manager import check_permission
from utils.cache_helpers import get_bible_list_cached, invalidate_cache, full_refresh

KNOWLEDGE_PAGE_SIZE = 10


def render_relations_tab(project_id, persona):
    st.header("🔗 Relations")
    st.caption("Quan hệ giữa các thực thể trong Bible. Chỉnh sửa hoặc xóa từng mục. Bấm **Refresh** để tải lại.")

    if not project_id:
        st.info("📁 Chọn Project trước.")
        return

    st.session_state.setdefault("update_trigger", 0)
    services = init_services()
    if not services:
        st.warning("Không kết nối được dịch vụ.")
        return
    supabase = services["supabase"]

    relations_no_vec_count = 0
    try:
        r = supabase.table("entity_relations").select("id").eq("story_id", project_id).is_("embedding", "NULL").limit(1001).execute()
        relations_no_vec_count = len(r.data or [])
        if relations_no_vec_count > 1000:
            relations_no_vec_count = 1001
    except Exception:
        pass
    lbl_rel = "1000+" if relations_no_vec_count > 1000 else str(relations_no_vec_count)
    st.caption(f"**Vector:** {lbl_rel} quan hệ chưa có embedding.")

    bible_data_all = get_bible_list_cached(project_id, st.session_state.get("update_trigger", 0))
    id_to_name = {e["id"]: e.get("entity_name", "") for e in bible_data_all}

    user_id = getattr(st.session_state.get("user"), "id", None) or ""
    user_email = getattr(st.session_state.get("user"), "email", None) or ""
    can_write = check_permission(user_id, user_email, project_id, "write")
    can_delete = check_permission(user_id, user_email, project_id, "delete")

    from core.background_jobs import is_embedding_backfill_running
    _relations_running = is_embedding_backfill_running("relations")
    if not _relations_running and st.session_state.get("embedding_sync_clicked_relations"):
        st.session_state.pop("embedding_sync_clicked_relations", None)
    sync_relations = _relations_running or st.session_state.get("embedding_sync_clicked_relations", False)
    if sync_relations:
        st.caption("⏳ Đang đồng bộ vector (Relations). Vui lòng đợi xong rồi bấm Refresh.")
    if st.button("🔄 Refresh", key="relations_refresh_btn"):
        full_refresh()
    if can_write:
        if st.button("🔄 Đồng bộ vector (Relations)", key="relations_sync_vec_btn", disabled=(relations_no_vec_count == 0 or sync_relations)):
            import threading
            from core.background_jobs import run_embedding_backfill
            st.session_state["embedding_sync_clicked_relations"] = True
            def _run():
                run_embedding_backfill(project_id, bible_limit=0, chunks_limit=0, relations_limit=200, timeline_limit=0)
            threading.Thread(target=_run, daemon=True).start()
            st.toast("Đã bắt đầu đồng bộ vector (Relations). Bấm Refresh sau vài giây.")
            st.rerun()

    # Filter theo chương (source_chapter)
    try:
        ch_list = supabase.table("chapters").select("chapter_number, title").eq("story_id", project_id).order("chapter_number").execute().data or []
        rel_chapter_options = ["Tất cả"] + [f"Chương {r.get('chapter_number', '')}: {r.get('title') or ''}" for r in ch_list]
        rel_chapter_nums = [None] + [r.get("chapter_number") for r in ch_list]
    except Exception:
        rel_chapter_options = ["Tất cả"]
        rel_chapter_nums = [None]
    rel_filter_chapter_idx = st.session_state.get("relations_filter_chapter", 0)
    rel_filter_chapter_idx = max(0, min(rel_filter_chapter_idx, len(rel_chapter_options) - 1))
    rel_filter_chapter_label = st.selectbox(
        "Chương",
        range(len(rel_chapter_options)),
        index=rel_filter_chapter_idx,
        format_func=lambda i: rel_chapter_options[i] if i < len(rel_chapter_options) else "",
        key="relations_filter_chapter_select",
        help="Chỉ hiển thị quan hệ có source_chapter thuộc chương đã chọn.",
    )
    st.session_state["relations_filter_chapter"] = rel_filter_chapter_label
    rel_filter_chapter_num = rel_chapter_nums[rel_filter_chapter_label] if rel_filter_chapter_label < len(rel_chapter_nums) else None
    if rel_filter_chapter_num is not None and st.session_state.get("relations_filter_chapter_prev") != rel_filter_chapter_label:
        st.session_state["relations_page"] = 1
    st.session_state["relations_filter_chapter_prev"] = rel_filter_chapter_label

    # Phân trang ở DB (tối đa 10 mục/trang)
    page = max(1, int(st.session_state.get("relations_page", 1)))
    try:
        count_q = supabase.table("entity_relations").select("id", count="exact").eq("story_id", project_id)
        if rel_filter_chapter_num is not None:
            count_q = count_q.eq("source_chapter", rel_filter_chapter_num)
        count_res = count_q.limit(0).execute()
        total_rels = getattr(count_res, "count", None) or 0
    except Exception:
        total_rels = 0
    total_pages = max(1, (total_rels + KNOWLEDGE_PAGE_SIZE - 1) // KNOWLEDGE_PAGE_SIZE)
    page = max(1, min(page, total_pages))
    st.session_state["relations_page"] = page
    offset = (page - 1) * KNOWLEDGE_PAGE_SIZE
    try:
        rel_q = (
            supabase.table("entity_relations")
            .select("*")
            .eq("story_id", project_id)
            .order("id")
        )
        if rel_filter_chapter_num is not None:
            rel_q = rel_q.eq("source_chapter", rel_filter_chapter_num)
        rel_res = rel_q.range(offset, offset + KNOWLEDGE_PAGE_SIZE - 1).execute()
        all_rels = rel_res.data if rel_res and rel_res.data else []
    except Exception as e:
        st.error(f"Lỗi khi tải quan hệ: {e}")
        all_rels = []
        total_rels = 0
        total_pages = 1

    if not all_rels and total_rels == 0:
        st.info("Chưa có quan hệ nào. Chạy Extract Bible rồi Relation (Data Analyze) để tạo quan hệ.")
        return

    st.metric("Tổng quan hệ", total_rels)
    if total_pages > 1:
        pcol1, pcol2, pcol3 = st.columns([1, 2, 1])
        with pcol1:
            if st.button("⬅️ Trang trước", key="rel_prev_page", disabled=(page <= 1)):
                st.session_state["relations_page"] = max(1, page - 1)
                st.rerun()
        with pcol2:
            st.caption(f"**Trang {page} / {total_pages}** (tối đa {KNOWLEDGE_PAGE_SIZE} mục/trang)")
        with pcol3:
            if st.button("Trang sau ➡️", key="rel_next_page", disabled=(page >= total_pages)):
                st.session_state["relations_page"] = min(total_pages, page + 1)
                st.rerun()

    for r in all_rels:
        rel_id = r.get("id")
        src_id = r.get("source_entity_id")
        tgt_id = r.get("target_entity_id")
        rtype = (r.get("relation_type") or r.get("relation") or "—").strip()
        desc = (r.get("description") or "").strip()
        src_name = id_to_name.get(src_id, "?")
        tgt_name = id_to_name.get(tgt_id, "?")
        title = f"**{src_name}** — {rtype} — **{tgt_name}**"
        has_embedding = bool(r.get("embedding"))
        sync_badge = "" if has_embedding else " 🔄 Chưa embed"

        editing = st.session_state.get("rel_editing_id") == rel_id

        with st.expander(f"{title}{sync_badge}", expanded=editing):
            if not has_embedding:
                st.caption("🔄 Chưa đồng bộ vector — bấm **Đồng bộ vector (Relations)** trên để embed.")
            if editing and can_write:
                new_type = st.text_input("Loại quan hệ", value=rtype, key=f"rel_type_{rel_id}")
                new_desc = st.text_area("Mô tả", value=desc, height=80, key=f"rel_desc_{rel_id}")
                col_save, col_cancel = st.columns(2)
                with col_save:
                    if st.button("💾 Lưu", key=f"rel_save_{rel_id}"):
                        try:
                            supabase.table("entity_relations").update({
                                "relation_type": (new_type or "").strip() or "liên quan",
                                "description": (new_desc or "").strip(),
                                "embedding": None,
                            }).eq("id", rel_id).execute()
                            st.session_state.pop("rel_editing_id", None)
                            invalidate_cache()
                        except Exception as ex:
                            st.error(f"Lỗi: {ex}")
                with col_cancel:
                    if st.button("❌ Hủy", key=f"rel_cancel_{rel_id}"):
                        st.session_state.pop("rel_editing_id", None)
            else:
                if desc:
                    st.markdown(desc)
                if can_write and not editing:
                    if st.button("✏️ Sửa", key=f"rel_edit_{rel_id}"):
                        st.session_state["rel_editing_id"] = rel_id
            if can_delete:
                if st.button("🗑️ Xóa", key=f"rel_del_{rel_id}"):
                    try:
                        supabase.table("entity_relations").delete().eq("id", rel_id).execute()
                        invalidate_cache()
                    except Exception as ex:
                        st.error(f"Lỗi xóa: {ex}")
