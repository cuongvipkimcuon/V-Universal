import re
from datetime import datetime

import pandas as pd
import streamlit as st

from config import Config, init_services
from ai_engine import AIService, HybridSearch, suggest_import_category, _get_default_tool_model
from utils.file_importer import UniversalLoader
from utils.auth_manager import check_permission, submit_pending_change
from utils.cache_helpers import get_bible_list_cached, invalidate_cache
from core.user_data_save_pipeline import run_logic_check_then_save_bible, run_logic_check_then_save_relation

# Tiền tố khóa (chỉ sửa nội dung, không sửa tiền tố): lấy từ Config.PREFIX_SPECIAL_SYSTEM, bỏ OTHER.
def _get_locked_prefixes():
    return tuple(f"[{k}]" for k in (getattr(Config, "PREFIX_SPECIAL_SYSTEM", ()) or ()) if k != "OTHER")


def _entry_has_locked_prefix(entry) -> bool:
    """Kiểm tra entry có prefix bị khóa (chỉ sửa nội dung, không sửa tiền tố)."""
    name = entry.get("entity_name") or ""
    locked = _get_locked_prefixes()
    return any(name.startswith(p) for p in locked)


def render_bible_tab(project_id, persona):
    """Tab Bible - Cache + fragment search, score formula, inline Importance Bias. [RULE]/[CHAT] chỉ ở tab Rules/Memory."""
    st.header("📚 Project Bible")
    st.caption("Nhân vật, địa điểm, lore... Entries [RULE] và [CHAT] chỉ hiện ở tab **Rules** và **Memory**.")

    if not project_id:
        st.info("📁 Please select or create a project first")
        return

    st.session_state.setdefault("update_trigger", 0)
    services = init_services()
    if not services:
        st.warning("Không kết nối được dịch vụ.")
        return
    supabase = services["supabase"]

    # Số mục chưa có embedding — luôn hiển thị lên đầu
    try:
        r_null = supabase.table("story_bible").select("id").eq("story_id", project_id).is_("embedding", "NULL").limit(1001).execute()
        n = len(r_null.data or [])
        bible_no_vec_count = n if n <= 1000 else 1001
    except Exception:
        bible_no_vec_count = 0
    lbl = "1000+" if bible_no_vec_count > 1000 else str(bible_no_vec_count)
    st.caption(f"**Vector:** {lbl} mục chưa có embedding.")

    # Cache trigger: chỉ refetch khi bấm Refresh hoặc sau add/delete
    _cache_trigger = st.session_state.get("update_trigger", 0)
    raw_bible = get_bible_list_cached(project_id, _cache_trigger)
    # V9.2: Không còn chặn prefix [RULE]/[CHAT] tại đây; Bible hiển thị đầy đủ, legacy RULE/CHAT không còn ảnh hưởng logic.
    bible_data_all = list(raw_bible or [])
    all_prefixes = set()
    for entry in bible_data_all:
        match = re.match(r"^(\[[^\]]+\])", entry.get("entity_name", "") or "")
        if match:
            all_prefixes.add(match.group(1))
    prefixes_from_config = Config.get_prefixes()
    available_prefixes = sorted(list(set(prefixes_from_config + list(all_prefixes) + ["[OTHER]"])))

    col_act = st.columns([3, 2, 1])
    with col_act[2]:
        st.markdown("###")
        # V8.9: Bỏ nút Refresh riêng tab — dùng Refresh tổng ở sidebar; dữ liệu load khi tab được kích hoạt
        if st.button("➕ Add Entry", type="primary", key="bible_add_btn"):
            st.session_state["adding_bible_entry"] = True
        if st.button("📥 Import Knowledge", type="secondary", key="bible_import_btn"):
            st.session_state["import_knowledge_mode"] = True

    # --- Đồng bộ vector (Bible); cờ riêng cho Bible — chỉ chặn trùng trong bảng Bible ---
    from core.background_jobs import is_embedding_backfill_running
    _bible_running = is_embedding_backfill_running("bible")
    if not _bible_running and st.session_state.get("embedding_sync_clicked_bible"):
        st.session_state.pop("embedding_sync_clicked_bible", None)
    sync_bible = _bible_running or st.session_state.get("embedding_sync_clicked_bible", False)
    if sync_bible:
        st.caption("⏳ Đang đồng bộ vector (Bible). Vui lòng đợi xong rồi bấm Refresh.")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("🔄 Kiểm tra mục chưa có embedding", key="bible_check_vec_btn", disabled=sync_bible):
            invalidate_cache()
            st.toast("Đã làm mới. Số mục chưa có embedding hiển thị phía trên.")
    with c2:
        if st.button("🔄 Đồng bộ vector (Bible)", key="bible_sync_vec_btn", disabled=(bible_no_vec_count == 0 or sync_bible)):
            import threading
            from core.background_jobs import run_embedding_backfill
            st.session_state["embedding_sync_clicked_bible"] = True
            def _run():
                run_embedding_backfill(project_id, bible_limit=200, chunks_limit=0, relations_limit=0, timeline_limit=0)
            threading.Thread(target=_run, daemon=True).start()
            st.toast("Đã bắt đầu đồng bộ vector (Bible). Bấm Refresh sau vài giây để xem kết quả.")
            st.rerun()

    # --- Import Knowledge: upload file -> parse -> gợi ý category -> thêm entry ---
    if st.session_state.get('import_knowledge_mode'):
        st.markdown("---")
        st.subheader("📥 Import Knowledge")
        uploaded = st.file_uploader(
            "Chọn file (.docx, .pdf, .xlsx, .txt, .md)",
            type=["docx", "pdf", "xlsx", "xls", "txt", "md"],
            key="import_file_upload"
        )
        if uploaded:
            text, err = UniversalLoader.load(uploaded)
            if err:
                st.error(err)
            elif text:
                if 'import_parsed_text' not in st.session_state or st.session_state.get('import_file_id') != id(uploaded):
                    with st.spinner("Đang gợi ý phân loại..."):
                        suggested = suggest_import_category(text)
                    st.session_state['import_parsed_text'] = text
                    st.session_state['import_suggested_category'] = suggested
                    st.session_state['import_file_id'] = id(uploaded)
                suggested = st.session_state.get('import_suggested_category', "[OTHER]")
                prefixes = [p for p in Config.get_prefixes() if p != "[CHAT]"]
                if "[OTHER]" not in prefixes and suggested == "[OTHER]":
                    prefixes = list(prefixes) + ["[OTHER]"]
                if suggested == "[CHAT]":
                    suggested = "[OTHER]"
                cat = st.selectbox(
                    "Category (gợi ý từ nội dung)",
                    prefixes,
                    index=prefixes.index(suggested) if suggested in prefixes else 0,
                    format_func=lambda x: x.replace("[", "").replace("]", ""),
                    key="import_category"
                )
                title_import = st.text_input("Tên entry (tùy chọn)", placeholder="Để trống = dùng dòng đầu nội dung", key="import_title")
                desc_import = st.text_area("Nội dung đã parse", value=st.session_state.get('import_parsed_text', text), height=200, key="import_desc")
                if st.button("✅ Thêm vào Bible", type="primary", key="import_confirm"):
                    name = (title_import or (desc_import.split("\n")[0][:80] if desc_import else "Imported")).strip()
                    if not name:
                        name = "Imported"
                    entity_name = f"{cat} {name}" if not name.startswith("[") else name
                    user_id = getattr(st.session_state.get("user"), "id", None) or ""
                    user_email = getattr(st.session_state.get("user"), "email", None) or ""
                    can_write = check_permission(user_id, user_email, project_id, "write")
                    can_request = check_permission(user_id, user_email, project_id, "request_write")
                    try:
                        payload = {
                            "entity_name": entity_name,
                            "description": desc_import[:50000],
                            "source_chapter": 0,
                        }
                        ok = False
                        _, errs, payload_ready = run_logic_check_then_save_bible(project_id, payload, supabase)
                        if not payload_ready:
                            st.error("Check logic lỗi:\n" + "\n".join(errs))
                        elif can_write:
                            supabase.table("story_bible").insert(payload_ready).execute()
                            st.session_state["update_trigger"] = st.session_state.get("update_trigger", 0) + 1
                            st.success("Đã thêm entry từ file! Bấm **Đồng bộ vector (Bible)** để tạo embedding.")
                            ok = True
                        elif can_request:
                            pid = submit_pending_change(
                                story_id=project_id,
                                requested_by_email=user_email,
                                table_name="story_bible",
                                target_key={},
                                old_data={},
                                new_data=payload_ready,
                            )
                            if pid:
                                st.toast("Đã gửi yêu cầu chỉnh sửa đến Owner.", icon="📤")
                                ok = True
                            else:
                                st.error("Không gửi được yêu cầu.")
                        else:
                            st.warning("Bạn không có quyền thêm.")
                        if ok:
                            st.session_state['import_knowledge_mode'] = False
                            st.session_state.pop('import_parsed_text', None)
                            st.session_state.pop('import_suggested_category', None)
                    except Exception as e:
                        st.error(f"Lỗi: {e}")
                if st.button("Hủy Import", key="import_cancel"):
                    st.session_state['import_knowledge_mode'] = False
                    st.session_state.pop('import_parsed_text', None)
        else:
            if st.button("Đóng Import", key="import_close"):
                st.session_state['import_knowledge_mode'] = False

    @st.fragment
    def _bible_search_fragment():
        col_search, col_filter, _ = st.columns([3, 2, 1])
        with col_search:
            search_term = st.text_input(
                "🔍 Search bible entries",
                placeholder="Search...",
                key="bible_search_input",
                help="Tìm theo tên hoặc nội dung mô tả (hybrid search).",
            )
        with col_filter:
            filter_prefix = st.selectbox(
                "Prefix",
                ["All"] + available_prefixes,
                key="bible_filter_prefix",
                help="Lọc danh sách theo loại thực thể [RULE], [CHARACTER], v.v.",
            )

        if search_term and search_term.strip():
            with st.spinner("Smart search..."):
                raw_results = HybridSearch.smart_search_hybrid_raw_with_scores(
                    search_term.strip(), project_id, top_k=20
                )
                # Bible tab không hiện [RULE] / [CHAT] (xem ở tab Rules và Memory)
                search_results = [
                    item for item in (raw_results or [])
                    if not (item.get("entity_name") or "").strip().startswith("[RULE]")
                    and not (item.get("entity_name") or "").strip().startswith("[CHAT]")
                ]
            if search_results:
                for item in search_results:
                    eid = item.get("id")
                    name = item.get("entity_name") or ""
                    desc = (item.get("description") or "")[:300]
                    v = item.get("score_vector", 0)
                    r = item.get("score_recency", 0)
                    b = item.get("score_bias", 0)
                    f = item.get("score_final", 0)
                    with st.expander(f"**{name}**", expanded=False):
                        st.caption(f"Score: {f} (Content: {v} + Recency: {r} + Bias: {b})")
                        st.markdown(desc + ("..." if len(item.get("description") or "") > 300 else ""))
                        cur_bias = item.get("importance_bias")
                        if cur_bias is not None and isinstance(cur_bias, (int, float)):
                            slider_val = max(-5, min(5, int(round(float(cur_bias) * 10 - 5))))
                        else:
                            slider_val = 0
                        new_bias = st.slider("Importance Bias", -5, 5, slider_val, 1, key=f"bias_{eid}")
                        if st.button("Cập nhật Bias", key=f"apply_bias_{eid}"):
                            try:
                                new_val = round((new_bias + 5) / 10.0, 2)
                                supabase.table("story_bible").update({"importance_bias": new_val, "embedding": None}).eq("id", eid).execute()
                                st.session_state["update_trigger"] = st.session_state.get("update_trigger", 0) + 1
                                st.toast("Đã cập nhật Importance Bias.")
                            except Exception as ex:
                                st.error(str(ex))
            else:
                st.info("Không tìm thấy kết quả.")
        else:
            st.caption("Danh sách đầy đủ theo prefix ở bên dưới.")

    _bible_search_fragment()

    filter_prefix_val = st.session_state.get("bible_filter_prefix", "All")
    bible_data = [e for e in bible_data_all if filter_prefix_val == "All" or (e.get("entity_name") or "").startswith(filter_prefix_val)]
    search_active = bool((st.session_state.get("bible_search_input") or "").strip())
    if search_active:
        bible_data = []

    if bible_data:
        col1, col2, col3, col4 = st.columns(4)

        with col1:
            st.metric("Total", len(bible_data))

        with col2:
            prefix_counts = {}
            for entry in bible_data:
                match = re.match(r'^(\[[^\]]+\])', entry['entity_name'])
                prefix = match.group(1) if match else "[OTHER]"
                prefix_counts[prefix] = prefix_counts.get(prefix, 0) + 1

            if prefix_counts:
                most_common = max(prefix_counts.items(), key=lambda x: x[1])
                st.metric("Most Common", most_common[0])

        with col3:
            chars = sum(1 for b in bible_data if '[CHARACTER]' in b.get('entity_name', ''))
            st.metric("Characters", chars)

        with col4:
            rules = sum(1 for b in bible_data if '[RULE]' in b.get('entity_name', ''))
            st.metric("Rules", rules)

    parent_options = [{"id": None, "entity_name": "(None)"}] + [{"id": r["id"], "entity_name": r.get("entity_name", "")} for r in bible_data_all]

    if st.session_state.get('adding_bible_entry'):
        st.markdown("---")
        st.subheader("Add New Bible Entry")

        with st.form("add_bible_form"):
            col_type, col_custom = st.columns([2, 3])

            with col_type:
                entry_type_options = [p for p in Config.get_prefixes() if p != "[CHAT]"]
                if "[OTHER]" not in entry_type_options:
                    entry_type_options = entry_type_options + ["[OTHER]"]
                entry_type = st.selectbox(
                    "Entry Type",
                    entry_type_options,
                    format_func=lambda x: x.replace("[", "").replace("]", ""),
                    help="[CHAT] chỉ tạo qua Auto Crystallize trong Chat, không add tay.",
                )

            with col_custom:
                custom_prefix = st.checkbox("Custom Prefix")
                if custom_prefix:
                    custom_prefix_input = st.text_input("Custom Prefix (with brackets)", value="[CUSTOM]")
                    entry_type = custom_prefix_input

                col_name, col_chap = st.columns([2, 4])

                with col_name:
                    name = st.text_input("Name/Title", help="Tên thực thể hoặc tiêu đề entry.")

                with col_chap:
                    source_chap = st.number_input("Source Chap", min_value=0, value=0, step=1, help="Số chương nguồn; 0 = toàn cục/không xác định.")

                parent_id = st.selectbox(
                    "Parent Entity",
                    range(len(parent_options)),
                    format_func=lambda i: parent_options[i]["entity_name"] if i < len(parent_options) else "(None)",
                    key="add_parent_select"
                )
                parent_id_value = parent_options[parent_id]["id"] if parent_id < len(parent_options) else None

                importance_bias = st.slider(
                    "Mức độ quan trọng (Importance Bias)",
                    min_value=-5,
                    max_value=5,
                    value=0,
                    step=1,
                    help="Ảnh hưởng re-rank search: cao = ưu tiên hơn",
                    key="add_importance"
                )

                description = st.text_area("Description", height=150)

                col_save, col_cancel = st.columns(2)

                with col_save:
                    if st.form_submit_button("💾 Save Entry", type="primary"):
                        if name and description and entry_type:
                            if entry_type.strip().upper() == "[CHAT]":
                                st.error("[CHAT] chỉ tạo qua Auto Crystallize trong Chat, không add tay.")
                            else:
                                entity_name = f"{entry_type} {name}"
                                user_id = getattr(st.session_state.get("user"), "id", None) or ""
                                user_email = getattr(st.session_state.get("user"), "email", None) or ""
                                can_write = check_permission(user_id, user_email, project_id, "write")
                                can_request = check_permission(user_id, user_email, project_id, "request_write")
                                payload = {
                                    "entity_name": entity_name,
                                    "description": description,
                                    "source_chapter": source_chap,
                                }
                                if parent_id_value is not None:
                                    payload["parent_id"] = parent_id_value
                                try:
                                    payload["importance_bias"] = round((importance_bias + 5) / 10.0, 2)
                                except Exception:
                                    payload["importance_bias"] = 0.5
                                try:
                                    ok, errs, payload_ready = run_logic_check_then_save_bible(project_id, payload, supabase)
                                    if not ok:
                                        st.error("Check logic lỗi:\n" + "\n".join(errs))
                                    else:
                                        if any("trùng tên" in e for e in errs):
                                            st.warning([e for e in errs if "trùng tên" in e][0])
                                        if can_write:
                                            supabase.table("story_bible").insert(payload_ready).execute()
                                            st.session_state["update_trigger"] = st.session_state.get("update_trigger", 0) + 1
                                            st.success("Entry added! Bấm **Đồng bộ vector (Bible)** để tạo embedding.")
                                            st.session_state['adding_bible_entry'] = False
                                        elif can_request:
                                            pid = submit_pending_change(
                                                story_id=project_id,
                                                requested_by_email=user_email,
                                                table_name="story_bible",
                                                target_key={},
                                                old_data={},
                                                new_data=payload_ready,
                                            )
                                            if pid:
                                                st.toast("Đã gửi yêu cầu chỉnh sửa đến Owner.", icon="📤")
                                                st.session_state['adding_bible_entry'] = False
                                            else:
                                                st.error("Không gửi được yêu cầu.")
                                        else:
                                            st.warning("Bạn không có quyền thêm entry.")
                                except Exception as e:
                                    st.error(f"Lỗi: {e}")
                        else:
                            st.warning("Please fill all fields")

                with col_cancel:
                    if st.form_submit_button("❌ Cancel"):
                        st.session_state['adding_bible_entry'] = False

    st.markdown("---")

    if bible_data:
        selections = st.multiselect(
            f"Select entries for batch operations ({len(bible_data)} total):",
            [f"{b['entity_name']} (ID: {b['id']})" for b in bible_data],
            key="bible_selections"
        )

        if selections:
            selected_ids = []
            selected_entries = []

            for sel in selections:
                match = re.search(r'ID: (\d+)', sel)
                if match:
                    entry_id = int(match.group(1))
                    selected_ids.append(entry_id)
                    for entry in bible_data:
                        if entry['id'] == entry_id:
                            selected_entries.append(entry)
                            break

            col_del, col_merge, col_export = st.columns(3)

            with col_del:
                if st.button("🗑️ Delete Selected", use_container_width=True):
                    user_id = getattr(st.session_state.get("user"), "id", None) or ""
                    user_email = getattr(st.session_state.get("user"), "email", None) or ""
                    if check_permission(user_id, user_email, project_id, "delete"):
                        try:
                            supabase.table("story_bible") \
                                .delete() \
                                .in_("id", selected_ids) \
                                .execute()
                            st.success(f"Deleted {len(selected_ids)} entries")
                            invalidate_cache()
                        except Exception as e:
                            st.error(f"Lỗi xóa: {e}")
                    else:
                        st.warning("Chỉ Owner mới được xóa entry.")

            with col_merge:
                if st.button("🧬 AI Merge Selected", use_container_width=True):
                    user_id = getattr(st.session_state.get("user"), "id", None) or ""
                    user_email = getattr(st.session_state.get("user"), "email", None) or ""
                    if not check_permission(user_id, user_email, project_id, "write"):
                        st.warning("Chỉ Owner mới được Merge.")
                    elif len(selected_entries) >= 2:
                        items_text = "\n".join([f"- {e['description']}" for e in selected_entries])
                        prompt_merge = f"""
                            Hãy hợp nhất các mục thông tin dưới đây thành một mục duy nhất, mạch lạc, đầy đủ chi tiết:

                            {items_text}

                            Yêu cầu: Viết lại bằng Tiếng Việt, giữ nguyên các thuật ngữ quan trọng.
                            """

                        try:
                            response = AIService.call_openrouter(
                                messages=[{"role": "user", "content": prompt_merge}],
                                model=_get_default_tool_model(),
                                temperature=0.3,
                                max_tokens=4000
                            )

                            merged_text = response.choices[0].message.content
                            supabase.table("story_bible").insert({
                                "story_id": project_id,
                                "entity_name": f"[MERGED] {datetime.now().strftime('%Y%m%d')}",
                                "description": merged_text,
                            }).execute()
                            supabase.table("story_bible") \
                                .delete() \
                                .in_("id", selected_ids) \
                                .execute()
                            st.success("Merged successfully! Bấm **Đồng bộ vector (Bible)** để tạo embedding.")
                            invalidate_cache()
                        except Exception as e:
                            st.error(f"Merge error: {e}")

            with col_export:
                if st.button("📤 Export Selected", use_container_width=True):
                    export_data = []
                    for entry in selected_entries:
                        export_data.append({
                            "entity_name": entry['entity_name'],
                            "description": entry['description'],
                            "created_at": entry['created_at']
                        })

                    df_export = pd.DataFrame(export_data)
                    st.download_button(
                        label="📥 Download as CSV",
                        data=df_export.to_csv(index=False).encode('utf-8'),
                        file_name=f"bible_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                        mime="text/csv",
                        use_container_width=True
                    )

        for entry in bible_data:
            has_embedding = bool(entry.get("embedding"))
            sync_badge = "" if has_embedding else " 🔄 Chưa đồng bộ"
            with st.expander(f"**{entry['entity_name']}**{sync_badge}", expanded=False):
                if not has_embedding:
                    st.caption("🔄 Chưa đồng bộ vector — bấm **Đồng bộ vector (Bible)** trên để cập nhật.")
                st.markdown(entry.get('description', ''))

                col_edit, col_delete, col_vector = st.columns(3)

                with col_edit:
                    if st.button("✏️ Edit", key=f"edit_{entry['id']}"):
                        st.session_state['editing_bible_entry'] = entry

                with col_delete:
                    if st.button("🗑️ Delete", key=f"delete_{entry['id']}"):
                        uid = getattr(st.session_state.get("user"), "id", None) or ""
                        uem = getattr(st.session_state.get("user"), "email", None) or ""
                        if check_permission(uid, uem, project_id, "delete"):
                            try:
                                supabase.table("story_bible").delete().eq("id", entry['id']).execute()
                                invalidate_cache()
                            except Exception as e:
                                st.error(f"Lỗi xóa: {e}")
                        else:
                            st.warning("Chỉ Owner mới được xóa.")

                with col_vector:
                    if st.button("🔍 Similar", key=f"similar_{entry['id']}"):
                        st.session_state['find_similar_to'] = entry['id']

                # Tab Relationships: danh sách quan hệ + form thêm quan hệ
                st.markdown("---")
                st.subheader("🔗 Relationships")
                try:
                    try:
                        rel_res = supabase.table("entity_relations").select("*").or_(
                            f"source_entity_id.eq.{entry['id']},target_entity_id.eq.{entry['id']}"
                        ).execute()
                    except Exception:
                        rel_res = supabase.table("entity_relations").select("*").or_(
                            f"entity_id.eq.{entry['id']},target_entity_id.eq.{entry['id']}"
                        ).execute()
                    rels = rel_res.data if rel_res.data else []
                except Exception:
                    rels = []

                id_to_name = {e["id"]: e.get("entity_name", "") for e in bible_data}
                for r in rels:
                    eid = r.get("entity_id") or r.get("source_entity_id") or r.get("from_entity_id")
                    tid = r.get("target_entity_id") or r.get("to_entity_id")
                    rtype = r.get("relation_type") or r.get("relation") or "—"
                    name_a = id_to_name.get(eid, f"ID {eid}")
                    name_b = id_to_name.get(tid, f"ID {tid}")
                    st.caption(f"• {name_a} — **{rtype}** — {name_b}")

                with st.form(key=f"add_relation_{entry['id']}"):
                    other_entities = [e for e in bible_data if e["id"] != entry["id"]]
                    target_options = {e["entity_name"]: e["id"] for e in other_entities}
                    rel_target = st.selectbox("Entity liên quan", options=list(target_options.keys()), key=f"rel_target_{entry['id']}")
                    rel_type = st.text_input(
                        "Loại quan hệ",
                        placeholder="VD: kẻ thù, đồng đội, yêu",
                        key=f"rel_type_{entry['id']}",
                        help="Mô tả ngắn quan hệ giữa hai thực thể.",
                    )
                    if st.form_submit_button("➕ Thêm quan hệ"):
                        if rel_target and rel_type:
                            uid = getattr(st.session_state.get("user"), "id", None) or ""
                            uem = getattr(st.session_state.get("user"), "email", None) or ""
                            if not check_permission(uid, uem, project_id, "write"):
                                st.warning("Chỉ Owner mới được thêm quan hệ.")
                            else:
                                try:
                                    payload = {
                                        "source_entity_id": entry["id"],
                                        "target_entity_id": target_options[rel_target],
                                        "relation_type": rel_type.strip(),
                                        "description": "",
                                        "story_id": project_id,
                                    }
                                    ok, errs, payload_ready = run_logic_check_then_save_relation(project_id, payload, supabase)
                                    if not ok:
                                        st.error("Check logic / match quan hệ lỗi:\n" + "\n".join(errs))
                                    else:
                                        try:
                                            supabase.table("entity_relations").insert(payload_ready).execute()
                                            st.success("Đã thêm quan hệ.")
                                        except Exception:
                                            alt = {
                                                "entity_id": payload_ready.get("source_entity_id"),
                                                "target_entity_id": payload_ready.get("target_entity_id"),
                                                "relation_type": payload_ready.get("relation_type", "liên quan"),
                                                "story_id": project_id,
                                            }
                                            supabase.table("entity_relations").insert(alt).execute()
                                            st.success("Đã thêm quan hệ.")
                                except Exception as ex:
                                    st.error(f"Lỗi: {ex}")
                        else:
                            st.warning("Chọn entity và nhập loại quan hệ.")

        if st.session_state.get('editing_bible_entry'):
            entry = st.session_state['editing_bible_entry']
            edit_id = entry.get("id")

            st.markdown("---")
            st.subheader(f"Edit: {entry['entity_name']}")

            edit_parent_options = [{"id": None, "entity_name": "(None)"}]
            for o in parent_options:
                if o["id"] is not None and o["id"] != edit_id:
                    edit_parent_options.append(o)

            with st.form("edit_bible_form"):
                is_locked_prefix = _entry_has_locked_prefix(entry)
                if is_locked_prefix:
                    st.text_input("Entity Name (khóa - tiền tố RULE/CHAT mặc định)", value=entry['entity_name'], disabled=True, key="edit_name_locked")
                    new_name = entry['entity_name']
                else:
                    new_name = st.text_input("Entity Name", value=entry['entity_name'], key="edit_name")
                new_desc = st.text_area("Description", value=entry['description'], height=150)
                cur_parent = entry.get("parent_id")
                edit_parent_idx = next((i for i, o in enumerate(edit_parent_options) if o["id"] == cur_parent), 0)
                edit_parent_id = st.selectbox(
                    "Parent Entity",
                    range(len(edit_parent_options)),
                    index=edit_parent_idx,
                    format_func=lambda i: edit_parent_options[i]["entity_name"] if i < len(edit_parent_options) else "(None)",
                    key="edit_parent_select"
                )
                edit_parent_id_value = edit_parent_options[edit_parent_id]["id"] if edit_parent_id < len(edit_parent_options) else None
                cur_imp = entry.get("importance_bias")
                if cur_imp is not None and isinstance(cur_imp, (int, float)):
                    cur_slider = max(-5, min(5, int(round(cur_imp * 10 - 5))))
                else:
                    cur_slider = 0
                edit_importance = st.slider(
                    "Mức độ quan trọng (Importance Bias)",
                    min_value=-5,
                    max_value=5,
                    value=cur_slider,
                    step=1,
                    key="edit_importance"
                )

                if st.form_submit_button("💾 Update"):
                    uid = getattr(st.session_state.get("user"), "id", None) or ""
                    uem = getattr(st.session_state.get("user"), "email", None) or ""
                    can_write = check_permission(uid, uem, project_id, "write")
                    can_request = check_permission(uid, uem, project_id, "request_write")
                    upd = {
                        "entity_name": new_name,
                        "description": new_desc,
                        "parent_id": edit_parent_id_value,
                        "story_id": project_id,
                    }
                    try:
                        upd["importance_bias"] = round((edit_importance + 5) / 10.0, 2)
                    except Exception:
                        upd["importance_bias"] = 0.5
                    ok, errs, payload_ready = run_logic_check_then_save_bible(project_id, upd, supabase)
                    if not ok:
                        st.error("Check logic lỗi:\n" + "\n".join(errs))
                    else:
                        update_fields = {k: v for k, v in payload_ready.items() if k not in ("id", "story_id")}
                        update_fields["embedding"] = None  # Chỉnh sửa tay → xóa embed để lần đồng bộ vector sau sẽ embed lại
                        try:
                            if can_write:
                                supabase.table("story_bible").update(update_fields).eq("id", edit_id).execute()
                                st.session_state["update_trigger"] = st.session_state.get("update_trigger", 0) + 1
                                st.success("Updated! Bấm **Đồng bộ vector (Bible)** nếu cần cập nhật embedding.")
                                del st.session_state['editing_bible_entry']
                            elif can_request:
                                pid = submit_pending_change(
                                    story_id=project_id,
                                    requested_by_email=uem,
                                    table_name="story_bible",
                                    target_key={"id": edit_id},
                                    old_data={"entity_name": entry.get("entity_name"), "description": entry.get("description")},
                                    new_data=update_fields,
                                )
                                if pid:
                                    st.toast("Đã gửi yêu cầu chỉnh sửa đến Owner.", icon="📤")
                                    del st.session_state['editing_bible_entry']
                                else:
                                    st.error("Không gửi được yêu cầu.")
                            else:
                                st.warning("Bạn không có quyền sửa.")
                        except Exception as e:
                            st.error(f"Lỗi: {e}")

                if st.form_submit_button("❌ Cancel"):
                    del st.session_state['editing_bible_entry']

        if st.session_state.get('find_similar_to'):
            entry_id = st.session_state['find_similar_to']

            target_entry = None
            for entry in bible_data:
                if entry['id'] == entry_id:
                    target_entry = entry
                    break

            if target_entry:
                st.markdown("---")
                st.subheader(f"Similar to: {target_entry['entity_name']}")

                search_text = f"{target_entry['entity_name']} {target_entry['description'][:100]}"
                similar_results = HybridSearch.smart_search_hybrid_raw(search_text, project_id, top_k=10)

                similar_results = [r for r in similar_results if r['id'] != entry_id]

                if similar_results:
                    for result in similar_results:
                        with st.expander(f"**{result['entity_name']}** (Similarity)", expanded=False):
                            st.markdown(result['description'][:200] + "...")

                if st.button("Close Similar Search"):
                    del st.session_state['find_similar_to']

    else:
        st.info("No bible entries found. Add some to build your project's knowledge base!")

    st.markdown("---")
    with st.expander("💀 Danger Zone", expanded=False):
        if not st.session_state.get('confirm_delete_all_bible'):
            if st.button("💣 Clear All Bible Entries", type="secondary", use_container_width=True):
                st.session_state['confirm_delete_all_bible'] = True

        else:
            st.warning("⚠️ CẢNH BÁO: Hành động này sẽ xóa sạch toàn bộ dữ liệu Bible và không thể khôi phục. Bạn chắc chứ?")

            col_yes, col_no = st.columns(2)

            with col_no:
                if st.button("❌ Thôi, giữ lại", use_container_width=True):
                    st.session_state['confirm_delete_all_bible'] = False

            with col_yes:
                if st.button("✅ Tôi chắc chắn. Xóa!", type="primary", use_container_width=True):
                    uid = getattr(st.session_state.get("user"), "id", None) or ""
                    uem = getattr(st.session_state.get("user"), "email", None) or ""
                    if check_permission(uid, uem, project_id, "delete"):
                        try:
                            supabase.table("story_bible") \
                                .delete() \
                                .eq("story_id", project_id) \
                                .execute()
                            st.success("Đã xóa sạch Bible!")
                            st.session_state['confirm_delete_all_bible'] = False
                            invalidate_cache()
                        except Exception as e:
                            st.error(f"Lỗi xóa: {e}")
                    else:
                        st.warning("Chỉ Owner mới được xóa toàn bộ Bible.")
                    st.session_state['confirm_delete_all_bible'] = False
