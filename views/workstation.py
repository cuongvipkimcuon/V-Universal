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
from utils.cache_helpers import get_chapters_cached, invalidate_cache_and_rerun


def render_workstation_tab(project_id, persona):
    """
    Tab Workstation - Cache chapter list, fragment cho khung soạn thảo để giảm rerun toàn trang.
    """
    st.subheader("✍️ Writing Workstation")

    if not project_id:
        st.info("📁 Vui lòng chọn Project ở thanh bên trái.")
        return

    st.session_state.setdefault("update_trigger", 0)
    file_list = get_chapters_cached(project_id, st.session_state.get("update_trigger", 0))
    file_options = {}
    for f in file_list:
        display_name = f"📄 #{f['chapter_number']}: {f['title']}" if f.get('title') else f"📄 #{f['chapter_number']}"
        file_options[display_name] = f["chapter_number"]

    # --- Thư viện chương: Expander thu gọn + Bảng Dataframe ---
    with st.expander(f"📚 Thư viện chương đã viết ({len(file_list)} chương)", expanded=False):
        chapters_data = file_list or []

        if chapters_data:
            df_data = []
            for ch in chapters_data:
                num = ch.get("chapter_number", 0)
                title = ch.get("title") or f"Chương {num}"
                summary_raw = ch.get("summary") or ""
                summary = summary_raw[:100] + ("..." if len(summary_raw) > 100 else "")
                created = ch.get("created_at", "")
                if created:
                    try:
                        if isinstance(created, str):
                            dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                            created = dt.strftime("%d/%m/%Y %H:%M")
                        else:
                            created = str(created)[:16]
                    except Exception:
                        created = str(created)[:16] if created else "N/A"
                df_data.append(
                    {
                        "Số chương": num,
                        "Tiêu đề": title,
                        "Tóm tắt": summary,
                        "Ngày tạo": created or "N/A",
                    }
                )

            df = pd.DataFrame(df_data)
            st.dataframe(df, use_container_width=True, hide_index=True)

            st.markdown("---")
            col_del, col_clear = st.columns([3, 1])

            with col_del:
                st.caption("🗑️ Chọn chương để xóa:")
                selected_nums = st.multiselect(
                    "Chọn chương cần xóa",
                    options=[ch["Số chương"] for ch in df_data],
                    format_func=lambda x: f"#{x}: {next((c['Tiêu đề'] for c in df_data if c['Số chương'] == x), '')}",
                    key="ws_delete_selected_chapters",
                    help="Chọn một hoặc nhiều chương để xóa.",
                )
                if selected_nums and st.button(
                    "🗑️ Xóa các chương đã chọn", type="secondary", key="ws_delete_selected_btn"
                ):
                    uid = getattr(st.session_state.get("user"), "id", None) or ""
                    uem = getattr(st.session_state.get("user"), "email", None) or ""
                    if check_permission(uid, uem, project_id, "write"):
                        try:
                            services = init_services()
                            if services:
                                supabase = services["supabase"]
                                for num in selected_nums:
                                    supabase.table("chapters").delete().eq(
                                        "story_id", project_id
                                    ).eq("chapter_number", num).execute()
                                st.success(f"Đã xóa {len(selected_nums)} chương.")
                                # Dọn cache + tăng update_trigger + rerun theo yêu cầu
                                st.cache_data.clear()
                                st.session_state["update_trigger"] = st.session_state.get("update_trigger", 0) + 1
                                st.rerun()
                        except Exception as e:
                            st.error(f"Lỗi xóa: {e}")
                    else:
                        st.warning("Chỉ Owner mới được xóa chương.")

            with col_clear:
                st.caption("⚠️ Xóa sạch:")
                confirm_clear = st.checkbox(
                    "Tôi chắc chắn muốn xóa TẤT CẢ",
                    key="ws_confirm_clear_all",
                    help="Bật checkbox này để kích hoạt nút xóa sạch.",
                )
                if confirm_clear:
                    if st.button("🔥 Xóa sạch dự án", type="primary", key="ws_clear_all_btn"):
                        uid = getattr(st.session_state.get("user"), "id", None) or ""
                        uem = getattr(st.session_state.get("user"), "email", None) or ""
                        if check_permission(uid, uem, project_id, "write"):
                            try:
                                services = init_services()
                                if services:
                                    supabase = services["supabase"]
                                    supabase.table("chapters").delete().eq("story_id", project_id).execute()
                                    st.success("✅ Đã xóa sạch tất cả chương!")
                                    st.session_state["ws_confirm_clear_all"] = False
                                    # Dọn cache + tăng update_trigger + rerun theo yêu cầu
                                    st.cache_data.clear()
                                    st.session_state["update_trigger"] = st.session_state.get("update_trigger", 0) + 1
                                    st.rerun()
                            except Exception as e:
                                st.error(f"Lỗi xóa sạch: {e}")
                        else:
                            st.warning("Chỉ Owner mới được xóa sạch dự án.")
        else:
            st.info("Chưa có chương nào.")

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
        c1, c2, c3, c4, c5 = st.columns([3, 1, 1, 1, 1])
        with c1:
            selected_file = st.selectbox(
                "Select File",
                ["+ New File"] + list(file_options.keys()),
                label_visibility="collapsed",
                key="workstation_file_select",
            )
        chap_num = 0
        if selected_file == "+ New File":
            chap_num = len(file_list) + 1
            db_content = ""
            db_review = ""
            db_title = f"Chapter {chap_num}"
        else:
            chap_num = file_options.get(selected_file, 1)
            try:
                res = supabase.table("chapters").select(
                    "content, title, review_content"
                ).eq("story_id", project_id).eq("chapter_number", chap_num).execute()
                if res.data and len(res.data) > 0:
                    row = res.data[0]
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

        with c2:
            if st.button("💾 Save", use_container_width=True, key="ws_save_btn"):
                current_content = st.session_state.get(f"file_content_{chap_num}", "")
                current_title = st.session_state.get(f"file_title_{chap_num}", db_title)
                if current_content:
                    user_id = getattr(st.session_state.get("user"), "id", None) or ""
                    user_email = getattr(st.session_state.get("user"), "email", None) or ""
                    can_write = check_permission(user_id, user_email, project_id, "write")
                    can_request = check_permission(user_id, user_email, project_id, "request_write")
                    try:
                        if can_write:
                            supabase.table("chapters").upsert({
                                "story_id": project_id,
                                "chapter_number": chap_num,
                                "title": current_title,
                                "content": current_content,
                            }, on_conflict="story_id, chapter_number").execute()
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
                            st.rerun()
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

        with c3:
            if st.button("🚀 Review", use_container_width=True, type="primary", key="ws_review_btn"):
                st.session_state["trigger_ai_review"] = True
                st.rerun()
        with c4:
            if st.button("📥 Extract", use_container_width=True, key="ws_extract_btn"):
                st.session_state["extract_bible_mode"] = True
                st.session_state["temp_extracted_data"] = None
                st.rerun()
        with c5:
            if st.button("📂 Import", use_container_width=True, key="ws_import_btn"):
                st.session_state["workstation_import_mode"] = True
                st.rerun()

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
                    st.text_area(
                        "Nội dung đã đọc (xem trước)",
                        value=text[:50000],
                        height=200,
                        disabled=True,
                        key="import_preview",
                        help="Xem trước nội dung file đã parse. Dùng Thay thế/Thêm vào cuối hoặc ✂️ Cắt thông minh.",
                    )
                    st.caption(f"Tổng {len(text)} ký tự.")
                    col_replace, col_append, col_cut, col_cancel = st.columns(4)
                    with col_replace:
                        if st.button("✅ Thay thế", type="primary", use_container_width=True, key="imp_replace", help="Thay nội dung chương hiện tại bằng file."):
                            st.session_state[f"file_content_{chap_num}"] = text
                            st.session_state["workstation_import_mode"] = False
                            st.session_state.pop("workstation_imported_text", None)
                            st.session_state.pop("workstation_split_preview", None)
                            st.success("Đã thay thế. Nhớ bấm Save để lưu DB.")
                            st.rerun()
                    with col_append:
                        if st.button("➕ Thêm vào cuối", use_container_width=True, key="imp_append", help="Nối file vào cuối chương hiện tại."):
                            current = st.session_state.get(f"file_content_{chap_num}", db_content or "")
                            st.session_state[f"file_content_{chap_num}"] = (current.rstrip() + "\n\n" + text.lstrip()) if current else text
                            st.session_state["workstation_import_mode"] = False
                            st.session_state.pop("workstation_imported_text", None)
                            st.session_state.pop("workstation_split_preview", None)
                            st.success("Đã thêm vào cuối. Nhớ bấm Save.")
                            st.rerun()
                    with col_cut:
                        if st.button("✂️ Cắt", use_container_width=True, key="imp_smart_split", help="AI cắt theo chương/entity/sheet, đề xuất nhiều phần để lưu thành nhiều chương."):
                            st.session_state["workstation_split_mode"] = True
                            st.session_state["workstation_imported_text"] = text
                            st.rerun()
                    with col_cancel:
                        if st.button("❌ Hủy", use_container_width=True, key="imp_cancel"):
                            st.session_state["workstation_import_mode"] = False
                            st.session_state.pop("workstation_imported_text", None)
                            st.session_state.pop("workstation_split_preview", None)
                            st.session_state.pop("workstation_split_mode", None)
                            st.rerun()

                    # --- Workflow Cắt thông minh: AI Suggest (nhẹ) -> Python Execute (mạnh) ---
                    text_for_split = st.session_state.get("workstation_imported_text") or text
                    if st.session_state.get("workstation_split_mode") and text_for_split:
                        st.markdown("---")
                        st.subheader("✂️ Cắt thông minh")
                        st.caption("💡 AI phân tích mẫu rải rác (80 đầu + 80 giữa + 80 cuối) để tìm quy luật, Python dùng Regex cắt toàn bộ file.")
                        file_type_choice = st.radio(
                            "Loại nội dung",
                            ["story", "character_data", "excel_export"],
                            format_func=lambda x: {"story": "📖 Truyện (theo chương)", "character_data": "👤 Nhân vật/Entity", "excel_export": "📊 Excel/Sheet"}[x],
                            key="split_type_radio",
                            help="Chọn loại để AI tìm quy luật phân cách phù hợp.",
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
                                            invalidate_cache_and_rerun()
                                    except Exception as e:
                                        st.error(f"Lỗi lưu: {e}")
                        
                        if st.session_state.get("workstation_split_mode") and st.button("↩️ Quay lại", key="split_back"):
                            st.session_state.pop("workstation_split_preview", None)
                            st.session_state.pop("workstation_split_strategy", None)
                            st.session_state["workstation_split_mode"] = False
                            st.rerun()
            else:
                if st.button("Đóng Import", key="workstation_import_close"):
                    st.session_state["workstation_import_mode"] = False
                    st.session_state.pop("workstation_imported_text", None)
                    st.rerun()

        st.markdown("---")
        file_title = st.text_input(
            "Tiêu đề chương:",
            value=db_title,
            key=f"file_title_{chap_num}",
            label_visibility="collapsed",
            placeholder="Nhập tên chương...",
        )
        has_review = bool(db_review) or st.session_state.get("trigger_ai_review")
        if has_review:
            col_editor, col_review = st.columns([3, 2])
        else:
            col_editor = st.container()
        with col_editor:
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
        if has_review:
            with col_review:
                if st.session_state.get("trigger_ai_review"):
                    with st.spinner("AI đang đọc & đối chiếu Bible..."):
                        try:
                            context = HybridSearch.smart_search_hybrid(content[:1000], project_id)
                            rules = ContextManager.get_mandatory_rules(project_id)
                            review_prompt = f"""
                    LUẬT DỰ ÁN: {rules}
                    THÔNG TIN TỪ BIBLE (Context): {context}
                    NỘI DUNG CẦN REVIEW:
                    {content}
                    NHIỆM VỤ: {persona.get('review_prompt', 'Review nội dung này')}
                    YÊU CẦU:
                    1. Chỉ ra điểm mạnh/yếu.
                    2. Phát hiện lỗi logic (plot hole) hoặc lỗi code so với Context.
                    3. Đề xuất cải thiện cụ thể.
                    4. Trả về định dạng Markdown đẹp mắt (Bullet points).
                    5. Ngôn ngữ: TIẾNG VIỆT.
                    """
                            response = AIService.call_openrouter(
                                messages=[{"role": "user", "content": review_prompt}],
                                model=st.session_state.get("selected_model", Config.DEFAULT_MODEL),
                                temperature=0.5,
                            )
                            if response and response.choices:
                                new_review = response.choices[0].message.content
                                supabase.table("chapters").update({"review_content": new_review}).eq(
                                    "story_id", project_id
                                ).eq("chapter_number", chap_num).execute()
                                st.session_state["trigger_ai_review"] = False
                                st.toast("Review hoàn tất!", icon="🤖")
                                st.rerun()
                        except Exception as e:
                            st.error(f"Lỗi Review: {e}")
                            st.session_state["trigger_ai_review"] = False
                with st.expander("🤖 AI Editor Notes", expanded=True):
                    if db_review:
                        st.markdown(db_review)
                        if st.button("🗑️ Xóa Review", key="del_rev", use_container_width=True):
                            supabase.table("chapters").update({"review_content": ""}).eq(
                                "story_id", project_id
                            ).eq("chapter_number", chap_num).execute()
                            st.rerun()
                    else:
                        st.info("Chưa có nhận xét nào.")

    _editor_fragment()

    if st.session_state.get("extract_bible_mode"):
        sel = st.session_state.get("workstation_file_select", "+ New File")
        if sel == "+ New File":
            _chap = len(file_list) + 1
        else:
            _chap = file_options.get(sel, 1)
        content = st.session_state.get(f"file_content_{_chap}", "")
        if content:
            services = init_services()
            supabase = services["supabase"]
            st.markdown("---")
            with st.container():
                st.subheader("📚 Trích xuất Bible (Smart Mode - Tự do)")

                has_data = st.session_state.get('temp_extracted_data') is not None

                if not has_data:
                    st.info("💡 Hệ thống sẽ đọc hiểu văn bản, tự động phát hiện Nhân vật, Chiêu thức, Địa danh... và đặt loại (Type) theo ngữ cảnh.")

                    if st.button("▶️ Bắt đầu phân tích", type="primary", key="extract_start"):
                        my_bar = st.progress(0, text="Đang khởi động bộ não...")

                        def chunk_text(text, chunk_size=64000):
                            return [text[i:i+chunk_size] for i in range(0, len(text), chunk_size)]

                        chunks = chunk_text(content)
                        total_chunks = len(chunks)
                        all_extracted_items = []

                        try:
                            for i, chunk_content in enumerate(chunks):
                                my_bar.progress(int((i / total_chunks) * 90), text=f"Đang đọc hiểu phần {i+1}/{total_chunks}...")

                                ext_prompt = f"""
                            NỘI DUNG (Phần {i+1}/{total_chunks}):
                            {chunk_content}

                            NHIỆM VỤ: Trích xuất các thực thể quan trọng (Nhân vật, Địa danh, Vật phẩm, Chiêu thức, Khái niệm, Sự kiện...) từ nội dung trên.

                            ⛔️ YÊU CẦU ĐỊNH DẠNG (JSON BẮT BUỘC):
                            1. Trả về một JSON Object duy nhất chứa key "items".
                            2. KHÔNG viết lời dẫn, KHÔNG dùng markdown code block.
                            3. Trường "type": Hãy tự đặt tên loại thực thể bằng TIẾNG VIỆT dựa trên ngữ cảnh.
                            4. "description": Tóm tắt ngắn gọn vai trò/đặc điểm (dưới 50 từ).

                            ⚠️ QUAN TRỌNG:
                                - Nếu không tìm thấy thực thể nào, hãy trả về danh sách rỗng: {{ "items": [] }}
                                - TUYỆT ĐỐI KHÔNG COPY VÍ DỤ MẪU BÊN DƯỚI VÀO KẾT QUẢ.

                            VÍ DỤ CẤU TRÚC (CHỈ ĐỂ THAM KHẢO FORMAT, KHÔNG ĐƯỢC CHÉP):
                        {{
                            "items": [
                                {{ "entity_name": "Tên_Thực_Thể_Tìm_Thấy", "type": "Loại_Của_Nó", "description": "Mô_tả_ngắn_gọn..." }}
                                    ]
                        }}
                            """

                                response = AIService.call_openrouter(
                                    messages=[{"role": "user", "content": ext_prompt}],
                                    model=st.session_state.get('selected_model', Config.DEFAULT_MODEL),
                                    temperature=0.0,
                                    max_tokens=16000,
                                    response_format={"type": "json_object"}
                                )

                                if response and response.choices:
                                    raw_text = response.choices[0].message.content.strip()
                                    try:
                                        json_obj = json.loads(raw_text)
                                        chunk_items = []
                                        if "items" in json_obj:
                                            chunk_items = json_obj["items"]
                                        elif isinstance(json_obj, list):
                                            chunk_items = json_obj
                                        if chunk_items:
                                            all_extracted_items.extend(chunk_items)
                                    except Exception:
                                        clean_json = AIService.clean_json_text(raw_text)
                                        try:
                                            parsed = json.loads(clean_json)
                                            if isinstance(parsed, dict):
                                                all_extracted_items.extend(parsed.get('items', []))
                                            elif isinstance(parsed, list):
                                                all_extracted_items.extend(parsed)
                                        except Exception:
                                            pass

                            my_bar.progress(100, text="Hoàn tất! Đang tổng hợp...")
                            time.sleep(0.5)
                            my_bar.empty()
                            st.session_state['temp_extracted_data'] = all_extracted_items
                            st.rerun()
                        except Exception as e:
                            st.error(f"Lỗi hệ thống: {e}")

                    if st.button("Hủy bỏ", key="extract_cancel"):
                        st.session_state['extract_bible_mode'] = False
                        st.rerun()

                else:
                    items = st.session_state['temp_extracted_data']
                    if not items:
                        st.warning("⚠️ Không tìm thấy thực thể nào trong nội dung này.")
                        if st.button("Thử lại / Quét lại", key="extract_retry"):
                            st.session_state['temp_extracted_data'] = None
                            st.rerun()
                        if st.button("Đóng", key="extract_close"):
                            st.session_state['extract_bible_mode'] = False
                            st.session_state['temp_extracted_data'] = None
                            st.rerun()
                    else:
                        unique_items_dict = {}
                        for item in items:
                            name = item.get('entity_name', '').strip()
                            if name:
                                if name not in unique_items_dict:
                                    unique_items_dict[name] = item
                                else:
                                    if len(item.get('description', '')) > len(unique_items_dict[name].get('description', '')):
                                        unique_items_dict[name] = item
                        unique_items = list(unique_items_dict.values())
                        df_preview = pd.DataFrame(unique_items)
                        st.success(f"✅ Tìm thấy {len(unique_items)} thực thể độc nhất!")
                        with st.expander("👀 Xem trước & Kiểm tra dữ liệu", expanded=True):
                            if 'entity_name' in df_preview.columns:
                                st.dataframe(df_preview[['entity_name', 'type', 'description']], use_container_width=True)
                            else:
                                st.dataframe(df_preview, use_container_width=True)
                        c_save, c_cancel = st.columns([1, 1])
                        with c_save:
                            if st.button("💾 Lưu tất cả vào Bible", type="primary", use_container_width=True, key="extract_save_all"):
                                count = 0
                                prog = st.progress(0)
                                total = len(unique_items)
                                for idx, item in enumerate(unique_items):
                                    desc = item.get('description', '')
                                    raw_name = item.get('entity_name', 'Unknown')
                                    raw_type_str = item.get('type', 'Khác').strip()
                                    prefix_key = Config.map_extract_type_to_prefix(raw_type_str, desc)
                                    final_name = f"[{prefix_key}] {raw_name}" if not raw_name.startswith("[") else raw_name
                                    if desc:
                                        vec = AIService.get_embedding(desc)
                                        if vec:
                                            supabase.table("story_bible").insert({
                                                "story_id": project_id,
                                                "entity_name": final_name,
                                                "description": desc,
                                                "embedding": vec,
                                                "source_chapter": st.session_state.get('current_file_num', 0)
                                            }).execute()
                                            count += 1
                                    prog.progress(int((idx + 1) / total * 100))
                                st.balloons()
                                st.success(f"Đã lưu thành công {count} mục!")
                                st.session_state['extract_bible_mode'] = False
                                st.session_state['temp_extracted_data'] = None
                                st.session_state["update_trigger"] = st.session_state.get("update_trigger", 0) + 1
                                time.sleep(1.5)
                                st.rerun()
                        with c_cancel:
                            if st.button("Hủy bỏ / Làm lại", use_container_width=True, key="extract_cancel2"):
                                st.session_state['extract_bible_mode'] = False
                                st.session_state['temp_extracted_data'] = None
                                st.rerun()
