import json
import threading
import time
from datetime import datetime

import pandas as pd
import streamlit as st

from config import Config, init_services
from ai_engine import AIService, HybridSearch, ContextManager, generate_chapter_metadata, analyze_split_strategy, execute_split_logic
from utils.file_importer import UniversalLoader
from utils.auth_manager import check_permission, submit_pending_change
from utils.cache_helpers import get_chapters_cached, invalidate_cache, full_refresh


def render_workstation_tab(project_id, persona):
    """
    Tab Workstation - Cache chapter list, fragment cho khung soạn thảo để giảm rerun toàn trang.
    """
    # Custom CSS cho UI gọn và thoáng
    st.markdown("""
    <style>
    /* Giảm padding chật giữa các cột */
    div[data-testid="stHorizontalBlock"] > div { padding: 0 0.35rem; }
    /* Khoảng cách cho text area */
    div[data-testid="stVerticalBlock"] > div { padding-top: 0.5rem; }
    /* Expander gọn hơn */
    .streamlit-expanderHeader { font-size: 0.95rem; }
    </style>
    """, unsafe_allow_html=True)

    st.subheader("✍️ Writing Workstation")

    if not project_id:
        st.info("📁 Vui lòng chọn Project ở thanh bên trái.")
        return

    st.session_state.setdefault("update_trigger", 0)
    file_list = get_chapters_cached(project_id, st.session_state.get("update_trigger", 0))
    file_options = {}
    for f in file_list:
        display_name = f"📄 #{f['chapter_number']}: {f.get('title') or f'Chapter {f['chapter_number']}'}"
        file_options[display_name] = f["chapter_number"]

    @st.fragment
    def _editor_fragment():
        try:
            services = init_services()
        except Exception:
            services = None
        if not services:
            st.warning("Không kết nối được dịch vụ.")
            return
        supabase = services["supabase"]

        selected_file = st.selectbox(
            "Chọn chương",
            ["+ Tạo chương mới"] + list(file_options.keys()),
            label_visibility="collapsed",
            key="workstation_file_select",
        )

        chap_num = 0
        selected_chapter_row = None
        if selected_file == "+ Tạo chương mới":
            chap_num = len(file_list) + 1
            db_content = ""
            db_review = ""
            db_title = f"Chapter {chap_num}"
        else:
            chap_num = file_options.get(selected_file, 1)
            try:
                res = (
                    supabase.table("chapters")
                    .select("*")
                    .eq("story_id", project_id)
                    .eq("chapter_number", chap_num)
                    .limit(1)
                    .execute()
                )
                if res.data and len(res.data) > 0:
                    row = res.data[0]
                    selected_chapter_row = row
                    db_content = row.get("content") or ""
                    db_title = row.get("title") or f"Chapter {chap_num}"
                    db_review = row.get("review_content") or ""
                else:
                    db_content = ""
                    db_title = f"Chapter {chap_num}"
                    db_review = ""
            except Exception as e:
                st.error(f"Lỗi load: {e}")
                db_content = ""
                db_title = f"Chapter {chap_num}"
                db_review = ""

        # Arc & Persona cho Workstation
        try:
            from core.arc_service import ArcService
            arcs = ArcService.list_arcs(project_id, status="active") if project_id else []
        except Exception:
            arcs = []
        arc_options = ["(Không gán arc)"] + [a.get("name", "") for a in arcs]
        cur_arc_id = selected_chapter_row.get("arc_id") if selected_chapter_row else None
        default_arc_idx = 0
        if cur_arc_id and arcs:
            for i, a in enumerate(arcs):
                if str(a.get("id")) == str(cur_arc_id):
                    default_arc_idx = i + 1
                    break
        arc_idx = st.selectbox("📐 Arc chương này", range(len(arc_options)), index=default_arc_idx, format_func=lambda i: arc_options[i] if i < len(arc_options) else "", key="ws_chapter_arc")
        chapter_arc_id = arcs[arc_idx - 1]["id"] if arc_idx and arc_idx > 0 and arc_idx <= len(arcs) else None

        # Toolbar: Lưu, Import, Xóa, Xóa sạch (Review & Extract chuyển sang tab Data Analyze)
        btn_cols = st.columns([2, 1, 1, 1, 2])
        with btn_cols[0]:
            updated_str = "—"
            if selected_chapter_row:
                updated = selected_chapter_row.get("updated_at") or selected_chapter_row.get("created_at", "")
                if updated:
                    try:
                        if isinstance(updated, str):
                            dt_u = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                            updated_str = dt_u.strftime("%d/%m/%Y %H:%M")
                        else:
                            updated_str = str(updated)[:16]
                    except Exception:
                        updated_str = str(updated)[:16] if updated else "—"
            st.caption(f"📅 Cập nhật: {updated_str}")

        def _update_metadata_background(pid, num, content_text):
            try:
                meta = generate_chapter_metadata(content_text)
                if not meta:
                    return
                svc = init_services()
                if not svc:
                    return
                sb = svc["supabase"]
                payload = {}
                if meta.get("summary") is not None:
                    payload["summary"] = meta["summary"]
                if meta.get("art_style") is not None:
                    payload["art_style"] = meta["art_style"]
                if payload:
                    sb.table("chapters").update(payload).eq("story_id", pid).eq(
                        "chapter_number", num
                    ).execute()
            except Exception as e:
                print(f"Background metadata update error: {e}")

        with btn_cols[1]:
            if st.button("💾 Lưu", width="stretch", key="ws_save_btn"):
                current_content = st.session_state.get(f"file_content_{chap_num}", "")
                current_title = st.session_state.get(f"file_title_{chap_num}", db_title)
                if current_content:
                    user_id = getattr(st.session_state.get("user"), "id", None) or ""
                    user_email = getattr(st.session_state.get("user"), "email", None) or ""
                    can_write = check_permission(user_id, user_email, project_id, "write")
                    can_request = check_permission(user_id, user_email, project_id, "request_write")
                    try:
                        if can_write:
                            payload = {"story_id": project_id, "chapter_number": chap_num, "title": current_title, "content": current_content}
                            if chapter_arc_id:
                                payload["arc_id"] = chapter_arc_id
                            supabase.table("chapters").upsert(payload, on_conflict="story_id, chapter_number").execute()
                            st.session_state["update_trigger"] = st.session_state.get("update_trigger", 0) + 1
                            st.toast("Đã lưu & Đang cập nhật metadata...", icon="💾")
                            st.session_state.current_file_content = current_content
                            thread = threading.Thread(
                                target=_update_metadata_background,
                                args=(project_id, chap_num, current_content),
                                daemon=True,
                            )
                            thread.start()
                            time.sleep(0.5)
                        elif can_request:
                            pid = submit_pending_change(
                                story_id=project_id,
                                requested_by_email=user_email,
                                table_name="chapters",
                                target_key={"story_id": project_id, "chapter_number": chap_num},
                                old_data={"title": db_title, "content": db_content},
                                new_data={"title": current_title, "content": current_content},
                            )
                            if pid:
                                st.toast("Đã gửi yêu cầu chỉnh sửa đến Owner.", icon="📤")
                            else:
                                st.error("Không gửi được yêu cầu (kiểm tra bảng pending_changes).")
                        else:
                            st.warning("Bạn không có quyền ghi hoặc gửi yêu cầu sửa.")
                    except Exception as e:
                        st.error(f"Lỗi lưu: {e}")

        with btn_cols[2]:
            if st.button("📂 Import", width="stretch", key="ws_import_btn"):
                st.session_state["workstation_import_mode"] = True
        with btn_cols[3]:
            if chap_num and st.button("🗑️ Xóa", width="stretch", key="ws_delete_current"):
                uid = getattr(st.session_state.get("user"), "id", None) or ""
                uem = getattr(st.session_state.get("user"), "email", None) or ""
                if check_permission(uid, uem, project_id, "write"):
                    chap_arc_id = selected_chapter_row.get("arc_id") if selected_chapter_row else None
                    arc_archived = False
                    if chap_arc_id:
                        try:
                            from core.arc_service import ArcService
                            arc_row = ArcService.get_arc(chap_arc_id)
                            arc_archived = arc_row and arc_row.get("status") == "archived"
                        except Exception:
                            pass
                    if arc_archived:
                        st.warning("Chương thuộc Arc đã archive. Bỏ archive Arc trước khi xóa chương.")
                    else:
                        try:
                            supabase.table("chapters").delete().eq("story_id", project_id).eq("chapter_number", chap_num).execute()
                            st.success(f"Đã xóa chương #{chap_num}. Bấm Refresh để cập nhật.")
                            invalidate_cache()
                        except Exception as e:
                            st.error(f"Lỗi xóa chương: {e}")
                else:
                    st.warning("Chỉ Owner mới được xóa chương.")
        with btn_cols[4]:
            confirm_clear_all = st.checkbox(
                "Xóa hết", key="ws_confirm_clear_all_top", help="Bật để kích hoạt nút xóa sạch.",
            )
            if confirm_clear_all and st.button("🔥 Xóa sạch", type="secondary", width="stretch", key="ws_clear_all_btn_top"):
                uid = getattr(st.session_state.get("user"), "id", None) or ""
                uem = getattr(st.session_state.get("user"), "email", None) or ""
                if check_permission(uid, uem, project_id, "write"):
                    try:
                        supabase.table("chapters").delete().eq("story_id", project_id).execute()
                        st.success("✅ Đã xóa sạch tất cả chương!")
                        invalidate_cache()
                        st.success("Đã xóa. Bấm Refresh để cập nhật.")
                    except Exception as e:
                        st.error(f"Lỗi xóa sạch: {e}")
                else:
                    st.warning("Chỉ Owner mới được xóa sạch dự án.")

        # Tóm tắt & Art style trong expander thu gọn
        if selected_chapter_row:
            with st.expander("📋 Tóm tắt & Art style", expanded=False):
                sum_text = selected_chapter_row.get("summary") or "—"
                art_text = selected_chapter_row.get("art_style") or "—"
                col_s, col_a = st.columns(2)
                with col_s:
                    st.markdown("**Tóm tắt**")
                    st.write(sum_text if len(str(sum_text)) < 500 else str(sum_text)[:500] + "...")
                with col_a:
                    st.markdown("**Art style**")
                    st.write(art_text if len(str(art_text)) < 300 else str(art_text)[:300] + "...")

        st.divider()

        if st.session_state.get("workstation_import_mode"):
            st.markdown("---")
            st.subheader("📂 Import nội dung từ file")
            st.caption("Hỗ trợ: PDF, DOCX, XLSX, XLS, CSV, TXT, MD.")
            uploaded = st.file_uploader(
                "Chọn file",
                type=["pdf", "docx", "xlsx", "xls", "csv", "txt", "md"],
                key="workstation_file_upload",
            )
            if uploaded:
                text, err = UniversalLoader.load(uploaded)
                if err:
                    st.error(err)
                elif text:
                    st.session_state["workstation_imported_text"] = text
                    # Lưu phần mở rộng để áp logic cắt: PDF không cắt, CSV/XLS dùng sheet/row
                    fname = getattr(uploaded, "name", "") or ""
                    ext = "." + fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
                    st.session_state["workstation_import_ext"] = ext
                    st.text_area(
                        "Nội dung đã đọc (xem trước)",
                        value=text[:50000],
                        height=200,
                        disabled=True,
                        key="import_preview",
                        help="Xem trước nội dung file đã parse. Dùng Thay thế/Thêm vào cuối hoặc ✂️ Cắt thông minh.",
                    )
                    st.caption(f"Tổng {len(text)} ký tự.")
                    import_ext = st.session_state.get("workstation_import_ext", "")
                    is_pdf = import_ext == ".pdf"
                    col_replace, col_append, col_cut, col_cancel = st.columns(4)
                    with col_replace:
                        if st.button("✅ Thay thế", type="primary", width="stretch", key="imp_replace", help="Thay nội dung chương hiện tại bằng file."):
                            st.session_state[f"file_content_{chap_num}"] = text
                            st.session_state["workstation_import_mode"] = False
                            st.session_state.pop("workstation_imported_text", None)
                            st.session_state.pop("workstation_split_preview", None)
                            st.session_state.pop("workstation_import_ext", None)
                            st.success("Đã thay thế. Nhớ bấm Save để lưu DB.")
                    with col_append:
                        if st.button("➕ Thêm vào cuối", width="stretch", key="imp_append", help="Nối file vào cuối chương hiện tại."):
                            current = st.session_state.get(f"file_content_{chap_num}", db_content or "")
                            st.session_state[f"file_content_{chap_num}"] = (current.rstrip() + "\n\n" + text.lstrip()) if current else text
                            st.session_state["workstation_import_mode"] = False
                            st.session_state.pop("workstation_imported_text", None)
                            st.session_state.pop("workstation_split_preview", None)
                            st.session_state.pop("workstation_import_ext", None)
                            st.success("Đã thêm vào cuối. Nhớ bấm Save.")
                    with col_cut:
                        if not is_pdf:
                            if st.button("✂️ Cắt", width="stretch", key="imp_smart_split", help="AI cắt theo chương/entity/sheet, đề xuất nhiều phần để lưu thành nhiều chương."):
                                st.session_state["workstation_split_mode"] = True
                                st.session_state["workstation_imported_text"] = text
                        else:
                            st.caption("⚠️ PDF: không hỗ trợ cắt tự động.")
                    with col_cancel:
                        if st.button("❌ Hủy", width="stretch", key="imp_cancel"):
                            st.session_state["workstation_import_mode"] = False
                            st.session_state.pop("workstation_imported_text", None)
                            st.session_state.pop("workstation_split_preview", None)
                            st.session_state.pop("workstation_split_mode", None)
                            st.session_state.pop("workstation_import_ext", None)

                    # --- Workflow Cắt thông minh: AI Suggest (nhẹ) -> Python Execute (mạnh) ---
                    text_for_split = st.session_state.get("workstation_imported_text") or text
                    if st.session_state.get("workstation_split_mode") and text_for_split:
                        st.markdown("---")
                        st.subheader("✂️ Cắt thông minh")
                        import_ext_split = st.session_state.get("workstation_import_ext", "")
                        # CSV/XLS mặc định excel_export (chia theo sheet/row); TXT/MD/DOCX mặc định story (chia theo từ khóa)
                        default_idx = 2 if import_ext_split in (".csv", ".xls", ".xlsx") else 0
                        st.caption("💡 Text: cắt theo từ khóa (nội dung nằm giữa 2 từ khóa). CSV/XLS: cắt theo Sheet hoặc số dòng.")
                        file_type_choice = st.radio(
                            "Loại nội dung",
                            ["story", "character_data", "excel_export"],
                            index=default_idx,
                            format_func=lambda x: {"story": "📖 Truyện (từ khóa)", "character_data": "👤 Nhân vật/Entity", "excel_export": "📊 Excel/CSV (sheet/số dòng)"}[x],
                            key="split_type_radio",
                            help="Text: nội dung nằm gọn giữa 2 từ khóa. CSV/XLS: chia theo sheet hoặc tọa độ (số dòng).",
                        )
                        context_hint = st.text_input("Gợi ý thêm (tùy chọn)", placeholder="VD: Mỗi chương bắt đầu bằng 'Chương N'", key="split_hint")
                        
                        # AI Analyzer: phân tích mẫu rải rác
                        if st.button("🤖 AI tìm quy luật phân cách", type="primary", key="split_analyze"):
                            with st.spinner("AI đang phân tích mẫu rải rác (80 đầu + 80 giữa + 80 cuối)..."):
                                strategy = analyze_split_strategy(text_for_split, file_type=file_type_choice, context_hint=context_hint)
                                st.session_state["workstation_split_strategy"] = strategy
                            st.success(f"Tìm thấy quy luật: **{strategy['split_type']}** = `{strategy['split_value']}`")
                        
                        strategy = st.session_state.get("workstation_split_strategy")
                        if strategy:
                            st.info(f"📋 Quy luật: **{strategy['split_type']}** → Pattern/Keyword: `{strategy['split_value']}`")
                            if st.button("👀 Xem trước 5 đoạn cắt đầu tiên", key="split_preview_btn"):
                                with st.spinner("Python đang dùng Regex quét toàn bộ file..."):
                                    preview_splits = execute_split_logic(text_for_split, strategy["split_type"], strategy["split_value"], debug=True)
                                    st.session_state["workstation_split_preview"] = preview_splits
                                if preview_splits:
                                    st.success(f"✅ Tìm thấy **{len(preview_splits)}** phần. Xem preview bên dưới.")
                                else:
                                    st.error("❌ Không tìm thấy dấu hiệu phân chia chương. Vui lòng kiểm tra lại định dạng hoặc thử keyword/pattern khác.")
                            
                            preview = st.session_state.get("workstation_split_preview")
                            if preview:
                                st.caption("📋 **Safety Check:** Xem trước 5 đoạn cắt đầu tiên — nếu ổn, bấm **Xác nhận cắt** để lưu toàn bộ.")
                                for i, part in enumerate(preview[:5]):
                                    with st.expander(f"📄 {i+1}. {part.get('title', '')[:50]}... ({len(part.get('content', ''))} ký tự)"):
                                        st.text_area("Nội dung", value=part.get("content", "")[:2000] + ("..." if len(part.get("content", "")) > 2000 else ""), height=100, key=f"split_preview_{i}", disabled=True)
                                if len(preview) > 5:
                                    st.caption(f"⚠️ ... và {len(preview) - 5} phần khác sẽ được cắt tương tự.")
                                
                                if st.button("✅ Xác nhận cắt", type="primary", key="split_confirm"):
                                    try:
                                        svc = init_services()
                                        if not svc:
                                            st.error("Không kết nối được dịch vụ.")
                                        else:
                                            supabase = svc["supabase"]
                                            r = supabase.table("chapters").select("chapter_number").eq("story_id", project_id).order("chapter_number", desc=True).limit(1).execute()
                                            start_num = (r.data[0]["chapter_number"] + 1) if r.data else 1
                                            
                                            progress_bar = st.progress(0)
                                            status_text = st.empty()
                                            total = len(preview)
                                            
                                            for i, part in enumerate(preview):
                                                status_text.text(f"Đang lưu phần {i+1}/{total}: {part.get('title', '')[:30]}...")
                                                supabase.table("chapters").insert({
                                                    "story_id": project_id,
                                                    "chapter_number": start_num + i,
                                                    "title": part.get("title", f"Chương {start_num + i}"),
                                                    "content": part.get("content", ""),
                                                }).execute()
                                                progress_bar.progress((i + 1) / total)
                                            
                                            status_text.empty()
                                            progress_bar.empty()
                                            st.success(f"✅ Đã tạo {len(preview)} chương (số {start_num} → {start_num + len(preview) - 1}).")
                                            st.session_state["workstation_import_mode"] = False
                                            st.session_state.pop("workstation_imported_text", None)
                                            st.session_state.pop("workstation_split_preview", None)
                                            st.session_state.pop("workstation_split_strategy", None)
                                            st.session_state.pop("workstation_split_mode", None)
                                            st.session_state.pop("workstation_import_ext", None)
                                            invalidate_cache()
                                    except Exception as e:
                                        st.error(f"Lỗi lưu: {e}")
                        
                        if st.session_state.get("workstation_split_mode") and st.button("↩️ Quay lại", key="split_back"):
                            st.session_state.pop("workstation_split_preview", None)
                            st.session_state.pop("workstation_split_strategy", None)
                            st.session_state["workstation_split_mode"] = False
            else:
                if st.button("Đóng Import", key="workstation_import_close"):
                    st.session_state["workstation_import_mode"] = False
                    st.session_state.pop("workstation_imported_text", None)

        file_title = st.text_input(
            "Tiêu đề chương",
            value=db_title,
            key=f"file_title_{chap_num}",
            label_visibility="collapsed",
            placeholder="Nhập tên chương...",
        )
        content = st.text_area(
            "Nội dung chính",
            value=db_content,
            height=650,
            key=f"file_content_{chap_num}",
            label_visibility="collapsed",
            placeholder="Viết nội dung của bạn tại đây...",
        )
        if content:
            st.caption(f"📝 {len(content.split())} từ | {len(content)} ký tự")
        if db_review:
            st.caption("💡 Chương này đã có review trong DB. Xem / chỉnh sửa tại tab **🤖 Review**.")

    _editor_fragment()
    # Extract Bible / Relation / Chunking đã chuyển sang tab 📊 Data Analyze (Workspace)

