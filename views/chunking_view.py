# views/chunking_view.py - Danh sách chunks đã lưu: xem, sửa nội dung, vector lại, xóa
"""Chunking: chỉ quản lý chunks đã lưu. Logic tách chunk (Workstation) nằm trong utils.chunk_tools."""
import json

import streamlit as st

from config import init_services
from utils.auth_manager import check_permission
from ai_engine import AIService
from ai.content import generate_chunk_summary

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
        ch_list = (
            supabase.table("chapters")
            .select("id, chapter_number, title")
            .eq("story_id", project_id)
            .order("chapter_number")
            .execute()
            .data
            or []
        )
        ck_chapter_options = [
            "Tất cả"
        ] + [f"Chương {r.get('chapter_number', '')}: {r.get('title') or ''}" for r in ch_list]
        ck_chapter_ids = [None] + [r.get("id") for r in ch_list]
        chapter_number_by_id = {
            r.get("id"): r.get("chapter_number") for r in ch_list if r.get("id") is not None
        }
    except Exception:
        ck_chapter_options = ["Tất cả"]
        ck_chapter_ids = [None]
        chapter_number_by_id = {}
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
    focus_chunk_id = str(st.session_state.get("chunking_focus_chunk_id") or "") if st.session_state.get("chunking_focus_chunk_id") else ""
    focus_consumed = False

    for c in chunks_list:
            cid = c.get("id")
            content = (c.get("content") or c.get("raw_content") or "").strip()
            meta = c.get("meta_json") or {}
            sm = meta.get("source_metadata", meta) if isinstance(meta, dict) else meta
            # Label gốc từ nguồn import/file
            base_label = (
                sm.get("sheet_name", "")
                or sm.get("source_file", "")
                or (meta.get("title") if isinstance(meta, dict) else "")
                or c.get("source_type", "")
                or str(cid or "")[:8]
            )
            # Thêm thông tin chương + id để dễ debug
            ch_id = c.get("chapter_id")
            ch_num = None
            if ch_id and isinstance(chapter_number_by_id, dict):
                ch_num = chapter_number_by_id.get(ch_id)
            ch_prefix = f"[Ch.{ch_num}]" if ch_num is not None else ""
            id_suffix = f" #{str(cid)[:8]}" if cid else ""
            label = f"{ch_prefix} {base_label}{id_suffix}".strip()

            sync_badge = " 🔄 Chưa đồng bộ" if cid in ids_no_embedding else ""

            expanded = bool(focus_chunk_id and str(cid) == focus_chunk_id)
            with st.expander(f"Chunk: {label} — {content}{sync_badge}", expanded=expanded):
                if expanded and not focus_consumed:
                    # Chỉ focus lần đầu, sau đó xóa để tránh giữ state không cần thiết
                    focus_consumed = True
                    st.session_state.pop("chunking_focus_chunk_id", None)
                if cid in ids_no_embedding:
                    st.caption("🔄 Chưa đồng bộ vector — bấm **Đồng bộ vector (Chunks)** trên để cập nhật.")
                st.text(content)

                # Hiển thị nhanh danh sách entity đã gắn cho chunk (nếu có) để dễ debug
                if isinstance(meta, dict):
                    ents = meta.get("chunk_entities") or []
                    if isinstance(ents, list) and ents:
                        st.caption("🔗 Entities: " + ", ".join(str(e) for e in ents[:20]))

                if can_write:
                    # Metadata (tóm tắt) dùng để embed – cho phép sửa tay và cập nhật embedding không qua LLM
                    meta_summary_default = ""
                    if isinstance(meta, dict):
                        meta_summary_default = (meta.get("chunk_summary") or "").strip()
                    meta_edit_key = f"chunk_meta_summary_{cid}"
                    meta_summary = st.text_area(
                        "Metadata (tóm tắt dùng để embed, có thể sửa tay)",
                        value=meta_summary_default,
                        height=80,
                        key=meta_edit_key,
                    )

                    embed_only_key = f"chunk_update_embed_only_{cid}"
                    if st.button("🔄 Cập nhật embedding từ metadata (không gọi LLM)", key=embed_only_key):
                        meta2 = c.get("meta_json") or {}
                        if isinstance(meta2, str):
                            try:
                                meta2 = json.loads(meta2) if meta2 else {}
                            except Exception:
                                meta2 = {}
                        if not isinstance(meta2, dict):
                            meta2 = {}
                        meta2 = dict(meta2)
                        meta_summary_txt = (meta_summary or "").strip()
                        meta2["chunk_summary"] = meta_summary_txt

                        ents2 = meta2.get("chunk_entities") or []
                        embed_parts = []
                        if isinstance(ents2, list) and ents2:
                            embed_parts.append("Entities: " + ", ".join(str(x) for x in ents2[:20]))
                        if meta_summary_txt:
                            embed_parts.append("Summary: " + meta_summary_txt)
                        embed_text = "\n".join(embed_parts) if embed_parts else meta_summary_txt

                        embedding = None
                        if embed_text:
                            try:
                                embedding = AIService.get_embedding(embed_text)
                            except Exception as e:
                                embedding = None
                                st.warning(f"Lỗi khi tính embedding từ metadata cho chunk (sẽ đặt embedding=NULL): {e}")

                        update_payload_meta = {"meta_json": meta2}
                        update_payload_meta["embedding"] = embedding

                        try:
                            supabase.table("chunks").update(update_payload_meta).eq("id", cid).execute()
                            if embedding is not None:
                                st.success("Đã cập nhật metadata + embedding cho chunk (không gọi LLM).")
                            else:
                                st.success("Đã cập nhật metadata, embedding đang để NULL (có thể đồng bộ sau).")
                        except Exception as e:
                            st.error(str(e))

                    edit_key = f"chunk_edit_{cid}"
                    update_key = f"chunk_update_vec_{cid}"
                    new_content = st.text_area(
                        "Sửa nội dung (Lưu sẽ tự tóm tắt + cập nhật embedding)",
                        value=content,
                        height=120,
                        key=edit_key,
                    )
                    if st.button("🔄 Cập nhật nội dung", key=update_key, type="primary"):
                        if not (new_content and new_content.strip()):
                            st.warning("Nội dung không được để trống.")
                        else:
                            txt = new_content.strip()
                            chapter_id = c.get("chapter_id")
                            ch_number = None
                            if chapter_id and chapter_number_by_id:
                                ch_number = chapter_number_by_id.get(chapter_id)

                            try:
                                # Tóm tắt chunk + lấy entity liên quan từ Bible (theo chương + quan hệ trong arc), đồng thời chuẩn bị embedding
                                summary_data = generate_chunk_summary(
                                    txt,
                                    project_id,
                                    chapter_id=str(chapter_id) if chapter_id is not None else None,
                                    chapter_number=int(ch_number) if isinstance(ch_number, (int, float)) else None,
                                )
                            except Exception as e:
                                summary_data = {"summary": "", "entities": [], "embedding": None}
                                st.warning(f"Lỗi khi tóm tắt chunk (sẽ chỉ cập nhật nội dung): {e}")

                            # Hợp meta_json: giữ thông tin cũ, bổ sung chunk_summary + chunk_entities nếu có
                            meta = c.get("meta_json") or {}
                            if isinstance(meta, str):
                                try:
                                    meta = json.loads(meta) if meta else {}
                                except Exception:
                                    meta = {}
                            if not isinstance(meta, dict):
                                meta = {}
                            meta = dict(meta)
                            summ = (summary_data.get("summary") or "").strip()
                            ents = summary_data.get("entities") or []
                            if summ:
                                meta["chunk_summary"] = summ
                            if isinstance(ents, list) and ents:
                                meta["chunk_entities"] = [str(x) for x in ents if x]

                            # Tính embedding: ưu tiên embedding từ summary_data; nếu không có thì embed từ text
                            embedding = summary_data.get("embedding")
                            if embedding is None:
                                try:
                                    embed_text_parts = []
                                    if summ:
                                        embed_text_parts.append(summ)
                                    if ents:
                                        embed_text_parts.append(", ".join([str(x) for x in ents][:20]))
                                    embed_text = "\n".join(embed_text_parts) if embed_text_parts else txt[:4000]
                                    if embed_text:
                                        embedding = AIService.get_embedding(embed_text)
                                except Exception as e:
                                    embedding = None
                                    st.warning(f"Lỗi khi tính embedding cho chunk (sẽ đặt embedding=NULL): {e}")

                            update_payload = {
                                "content": txt,
                                "raw_content": txt,
                                "meta_json": meta,
                            }
                            # Nếu lấy được embedding thì ghi luôn, ngược lại để NULL để batch backfill sau
                            if embedding is not None:
                                update_payload["embedding"] = embedding
                            else:
                                update_payload["embedding"] = None

                            try:
                                supabase.table("chunks").update(update_payload).eq("id", cid).execute()
                                if summ:
                                    st.success("Đã cập nhật nội dung + tóm tắt + embedding cho chunk.")
                                elif embedding is not None:
                                    st.success("Đã cập nhật nội dung + embedding cho chunk.")
                                else:
                                    st.success("Đã cập nhật nội dung. Embedding đang để NULL (có thể đồng bộ sau).")
                            except Exception as e:
                                st.error(str(e))

                # --- Linked Bible / Timeline (manual editor trên dữ liệu đã có) ---
                st.markdown("---")
                st.subheader("🔗 Linked Bible")
                try:
                    cbl_res = (
                        supabase.table("chunk_bible_links")
                        .select("id, bible_entry_id, mention_role, sort_order")
                        .eq("story_id", project_id)
                        .eq("chunk_id", cid)
                        .order("sort_order")
                        .execute()
                    )
                    bible_links = cbl_res.data or []
                    bible_ids = [row["bible_entry_id"] for row in bible_links if row.get("bible_entry_id")]
                    bible_map = {}
                    if bible_ids:
                        b_res = (
                            supabase.table("story_bible")
                            .select("id, entity_name, source_chapter")
                            .in_("id", bible_ids)
                            .execute()
                        )
                        bible_map = {row["id"]: row for row in (b_res.data or []) if row.get("id")}
                except Exception:
                    bible_links = []
                    bible_map = {}

                if bible_links and bible_map:
                    for bl in bible_links:
                        bid = bl.get("bible_entry_id")
                        b_row = bible_map.get(bid)
                        if not b_row:
                            continue
                        b_name = b_row.get("entity_name") or ""
                        b_ch = b_row.get("source_chapter")
                        chip_label = f"[Ch.{b_ch}] {b_name}" if b_ch is not None else b_name
                        col_lbl, col_rm = st.columns([5, 1])
                        with col_lbl:
                            st.caption(chip_label)
                        with col_rm:
                            if can_write and st.button("❌", key=f"cbl_del_{cid}_{bl.get('id')}"):
                                try:
                                    supabase.table("chunk_bible_links").delete().eq("id", bl.get("id")).execute()
                                    st.toast("Đã gỡ link Bible.")
                                    st.rerun()
                                except Exception as e:
                                    st.error(str(e))
                else:
                    st.caption("Chưa có Bible nào được link với chunk này.")

                if can_write:
                    st.markdown("**Thêm link Bible**")
                    search_kw = st.text_input(
                        "Search Bible theo tên",
                        key=f"cbl_search_kw_{cid}",
                        placeholder="Nhập từ khóa entity_name...",
                    )
                    same_chapter_only = st.checkbox(
                        "Chỉ Bible cùng chương",
                        value=True,
                        key=f"cbl_same_ch_{cid}",
                        help="Giữ danh sách ngắn, dễ chọn.",
                    )
                    bible_search_results = []
                    if search_kw and search_kw.strip():
                        try:
                            q = (
                                supabase.table("story_bible")
                                .select("id, entity_name, source_chapter")
                                .eq("story_id", project_id)
                                .not_.ilike("entity_name", "[RULE]%")
                                .not_.ilike("entity_name", "[CHAT]%")
                                .ilike("entity_name", f"%{search_kw.strip()}%")
                                .limit(50)
                            )
                            ch_id = c.get("chapter_id")
                            ch_num = None
                            if ch_id and isinstance(chapter_number_by_id, dict):
                                ch_num = chapter_number_by_id.get(ch_id)
                            if same_chapter_only and ch_num is not None:
                                q = q.eq("source_chapter", ch_num)
                            bible_search_results = q.execute().data or []
                        except Exception:
                            bible_search_results = []

                    if bible_search_results:
                        options = {}
                        for row in bible_search_results:
                            name = row.get("entity_name") or ""
                            sc = row.get("source_chapter")
                            opt_label = f"[Ch.{sc}] {name}" if sc is not None else name
                            options[opt_label] = row.get("id")
                        selected_labels = st.multiselect(
                            "Chọn Bible để link",
                            list(options.keys()),
                            key=f"cbl_select_{cid}",
                        )
                        if st.button("➕ Thêm link Bible", key=f"cbl_add_{cid}"):
                            selected_ids = [options[lbl] for lbl in selected_labels if lbl in options]
                            existing_ids = {row.get("bible_entry_id") for row in bible_links}
                            rows_to_insert = []
                            sort_start = len(bible_links)
                            for bid in selected_ids:
                                if not bid or bid in existing_ids:
                                    continue
                                rows_to_insert.append(
                                    {
                                        "story_id": project_id,
                                        "chunk_id": cid,
                                        "bible_entry_id": bid,
                                        "mention_role": "manual",
                                        "sort_order": sort_start,
                                    }
                                )
                                sort_start += 1
                            if rows_to_insert:
                                try:
                                    supabase.table("chunk_bible_links").insert(rows_to_insert).execute()
                                    st.success("Đã thêm link Bible.")
                                    st.rerun()
                                except Exception as e:
                                    st.error(str(e))
                            else:
                                st.info("Không có link mới để thêm (có thể đã tồn tại).")

                st.markdown("---")
                st.subheader("⏱️ Linked Timeline")
                try:
                    ctl_res = (
                        supabase.table("chunk_timeline_links")
                        .select("id, timeline_event_id, mention_role, sort_order")
                        .eq("story_id", project_id)
                        .eq("chunk_id", cid)
                        .order("sort_order")
                        .execute()
                    )
                    timeline_links = ctl_res.data or []
                    timeline_ids = [row["timeline_event_id"] for row in timeline_links if row.get("timeline_event_id")]
                    timeline_map = {}
                    if timeline_ids:
                        t_res = (
                            supabase.table("timeline_events")
                            .select("id, title, description, chapter_id, event_order")
                            .in_("id", timeline_ids)
                            .execute()
                        )
                        timeline_map = {row["id"]: row for row in (t_res.data or []) if row.get("id")}
                except Exception:
                    timeline_links = []
                    timeline_map = {}

                if timeline_links and timeline_map:
                    for tl in timeline_links:
                        tid = tl.get("timeline_event_id")
                        t_row = timeline_map.get(tid)
                        if not t_row:
                            continue
                        title = t_row.get("title") or ""
                        ev_order = t_row.get("event_order")
                        ch_id = t_row.get("chapter_id")
                        ch_num = None
                        if ch_id and isinstance(chapter_number_by_id, dict):
                            ch_num = chapter_number_by_id.get(ch_id)
                        prefix = ""
                        if ch_num is not None:
                            prefix = f"[Ch.{ch_num}] "
                        if ev_order is not None:
                            prefix += f"#{ev_order} "
                        chip_label = f"{prefix}{title}"
                        col_lbl, col_rm = st.columns([5, 1])
                        with col_lbl:
                            st.caption(chip_label)
                        with col_rm:
                            if can_write and st.button("❌", key=f"ctl_del_{cid}_{tl.get('id')}"):
                                try:
                                    supabase.table("chunk_timeline_links").delete().eq("id", tl.get("id")).execute()
                                    st.toast("Đã gỡ link Timeline.")
                                    st.rerun()
                                except Exception as e:
                                    st.error(str(e))
                else:
                    st.caption("Chưa có sự kiện Timeline nào được link với chunk này.")

                if can_write:
                    st.markdown("**Thêm link Timeline**")
                    tl_search_kw = st.text_input(
                        "Search Timeline theo tiêu đề/mô tả",
                        key=f"ctl_search_kw_{cid}",
                        placeholder="Nhập từ khóa...",
                    )
                    same_chapter_tl_only = st.checkbox(
                        "Chỉ sự kiện cùng chương",
                        value=True,
                        key=f"ctl_same_ch_{cid}",
                        help="Giữ danh sách ngắn, dễ chọn.",
                    )
                    timeline_search_results = []
                    if tl_search_kw and tl_search_kw.strip():
                        try:
                            q = (
                                supabase.table("timeline_events")
                                .select("id, title, description, chapter_id, event_order")
                                .eq("story_id", project_id)
                                .order("event_order")
                            )
                            ch_id = c.get("chapter_id")
                            if same_chapter_tl_only and ch_id:
                                q = q.eq("chapter_id", ch_id)
                            # Tìm theo title hoặc description
                            kw = tl_search_kw.strip()
                            q = q.or_(f"title.ilike.%{kw}%,description.ilike.%{kw}%")
                            timeline_search_results = q.limit(50).execute().data or []
                        except Exception:
                            timeline_search_results = []

                    if timeline_search_results:
                        tl_options = {}
                        for row in timeline_search_results:
                            ch_id = row.get("chapter_id")
                            ch_num = None
                            if ch_id and isinstance(chapter_number_by_id, dict):
                                ch_num = chapter_number_by_id.get(ch_id)
                            ev_order = row.get("event_order")
                            title = row.get("title") or ""
                            preview = (row.get("description") or "").strip().split("\n")[0][:80]
                            label_prefix = ""
                            if ch_num is not None:
                                label_prefix += f"[Ch.{ch_num}] "
                            if ev_order is not None:
                                label_prefix += f"#{ev_order} "
                            opt_label = f"{label_prefix}{title}"
                            if preview:
                                opt_label += f" — {preview}"
                            tl_options[opt_label] = row.get("id")
                        selected_tl_labels = st.multiselect(
                            "Chọn sự kiện Timeline để link",
                            list(tl_options.keys()),
                            key=f"ctl_select_{cid}",
                        )
                        if st.button("➕ Thêm link Timeline", key=f"ctl_add_{cid}"):
                            selected_ids = [tl_options[lbl] for lbl in selected_tl_labels if lbl in tl_options]
                            existing_tids = {row.get("timeline_event_id") for row in timeline_links}
                            rows_to_insert = []
                            sort_start = len(timeline_links)
                            for tid in selected_ids:
                                if not tid or tid in existing_tids:
                                    continue
                                rows_to_insert.append(
                                    {
                                        "story_id": project_id,
                                        "chunk_id": cid,
                                        "timeline_event_id": tid,
                                        "mention_role": "manual",
                                        "sort_order": sort_start,
                                    }
                                )
                                sort_start += 1
                            if rows_to_insert:
                                try:
                                    supabase.table("chunk_timeline_links").insert(rows_to_insert).execute()
                                    st.success("Đã thêm link Timeline.")
                                    st.rerun()
                                except Exception as e:
                                    st.error(str(e))
                            else:
                                st.info("Không có link mới để thêm (có thể đã tồn tại).")

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
