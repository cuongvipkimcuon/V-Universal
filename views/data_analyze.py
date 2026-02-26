# views/data_analyze.py - Tab Data Analyze: chọn chương, Unified analyze (Bible + Timeline + Chunks + Relations + link 1-N), Đồng bộ toàn cục.
import json
import threading

import streamlit as st

from config import Config, init_services
from ai_engine import (
    AIService,
    analyze_split_strategy,
    execute_split_logic,
    _get_default_tool_model,
)
from utils.auth_manager import check_permission
from utils.cache_helpers import get_chapters_cached, get_chapter_content_cached
from persona import PersonaSystem
from core.background_jobs import create_job, ensure_background_job_runner


def _get_existing_bible_entity_names_for_chapter(project_id, chap_num, supabase):
    """Lấy set entity_name (và tên không prefix) đã có trong Bible cho chương này."""
    try:
        r = supabase.table("story_bible").select("entity_name").eq(
            "story_id", project_id
        ).eq("source_chapter", chap_num).execute()
        names = set()
        for row in (r.data or []):
            en = (row.get("entity_name") or "").strip()
            if en:
                names.add(en)
                # Thêm phần sau prefix [XXX] để match khi extract trả về tên không prefix
                if en.startswith("[") and "]" in en:
                    rest = en[en.index("]") + 1:].strip()
                    if rest:
                        names.add(rest)
        return names
    except Exception:
        return set()


def _get_entity_ids_for_chapter(project_id, chap_num, supabase):
    """Lấy list id các entity Bible có source_chapter = chương này."""
    try:
        r = supabase.table("story_bible").select("id").eq(
            "story_id", project_id
        ).eq("source_chapter", chap_num).execute()
        return [row["id"] for row in (r.data or []) if row.get("id")]
    except Exception:
        return []


def _run_extract_on_content(content, ext_persona, project_id, chap_num, exclude_existing=False, supabase=None):
    """Chạy extract Bible trên content; nếu exclude_existing thì loại item trùng với Bible hiện có của chương."""
    from ai_engine import AIService
    strategy = analyze_split_strategy(content, file_type="story", context_hint="")
    parts = execute_split_logic(content, strategy.get("split_type", "by_length"), strategy.get("split_value", "50000"))
    if not parts:
        parts = execute_split_logic(content, "by_length", "50000")
    MAX_CHARS = 55000
    chunks = []
    for p in parts:
        c = (p.get("content") or "").strip()
        if not c:
            continue
        if len(c) <= MAX_CHARS:
            chunks.append(c)
        else:
            for s in execute_split_logic(c, "by_length", "50000"):
                sc = (s.get("content") or "").strip()
                if sc:
                    chunks.append(sc)
    all_items = []
    allowed_keys = Config.get_allowed_prefix_keys_for_extract()
    prefix_list_str = ", ".join(allowed_keys) + ", OTHER" if allowed_keys else "OTHER"
    for i, chunk_content in enumerate(chunks):
        ext_prompt = f"""
NỘI DUNG (Phần {i+1}/{len(chunks)}):
{chunk_content}

NHIỆM VỤ: {ext_persona.get('extractor_prompt', 'Trích xuất các thực thể quan trọng từ nội dung trên.')}

Ưu tiên trích xuất ĐẦY ĐỦ: nhân vật dù chính hay phụ, địa điểm dù lớn hay nhỏ, sự kiện thoáng qua, đồ vật, khái niệm. Khi nghi ngờ vẫn liệt kê.

⛔️ YÊU CẦU: Trả về JSON với key "items". Trường "type" phải là đúng MỘT trong: {prefix_list_str}. "description": tóm tắt dưới 50 từ.
Nếu không tìm thấy: {{ "items": [] }}. Chỉ trả về JSON."""
        try:
            resp = AIService.call_openrouter(
                messages=[{"role": "user", "content": ext_prompt}],
                model=_get_default_tool_model(),
                temperature=0.0,
                max_tokens=16000,
                response_format={"type": "json_object"},
            )
            if resp and resp.choices:
                raw = resp.choices[0].message.content.strip()
                obj = json.loads(AIService.clean_json_text(raw))
                items_chunk = obj.get("items", []) if isinstance(obj, dict) else (obj if isinstance(obj, list) else [])
                all_items.extend(items_chunk)
        except Exception:
            pass
    if exclude_existing and supabase:
        existing = _get_existing_bible_entity_names_for_chapter(project_id, chap_num, supabase)
        def _norm(s):
            return (s or "").strip().lower()
        new_items = []
        for item in all_items:
            name = (item.get("entity_name") or "").strip()
            if not name:
                continue
            if _norm(name) in {_norm(n) for n in existing}:
                continue
            if name in existing:
                continue
            # Check without prefix
            if "]" in name and name.startswith("["):
                rest = name[name.index("]") + 1:].strip()
                if _norm(rest) in {_norm(n) for n in existing}:
                    continue
            new_items.append(item)
        return new_items
    unique_dict = {}
    for item in all_items:
        name = item.get("entity_name", "").strip()
        if name and (name not in unique_dict or len(item.get("description", "")) > len(unique_dict[name].get("description", ""))):
            unique_dict[name] = item
    return list(unique_dict.values())


def _run_extract_bible_batch(contents_list, ext_persona, project_id, supabase=None):
    """
    Extract Bible cho nhiều chương trong một lần gọi API.
    contents_list: [(ch_num, content), ...]. Trả về {ch_num: [item, ...]} (mỗi item có entity_name, type, description).
    """
    if not contents_list:
        return {}
    from ai_engine import AIService
    allowed_keys = Config.get_allowed_prefix_keys_for_extract()
    prefix_list_str = ", ".join(allowed_keys) + ", OTHER" if allowed_keys else "OTHER"
    prompt_parts = [
        "Nội dung nhiều chương (mỗi chương bắt đầu bằng CHƯƠNG N:). Trích xuất thực thể riêng theo từng chương.",
        "",
        ext_persona.get("extractor_prompt", "Trích xuất các thực thể quan trọng: nhân vật, địa điểm, sự kiện, đồ vật."),
        "",
        "---",
    ]
    for ch_num, content in contents_list:
        if not (content or str(content).strip()):
            continue
        prompt_parts.append(f"CHƯƠNG {ch_num}:")
        prompt_parts.append(str(content).strip()[:120000])
        prompt_parts.append("")
    prompt_parts.append("---")
    prompt_parts.append(
        f'⛔️ Trả về ĐÚNG MỘT JSON với key "chapters", value là mảng object: mỗi object có "chapter" (số chương) và "items" (mảng thực thể). '
        f'Mỗi thực thể: "entity_name", "type" (đúng MỘT trong: {prefix_list_str}), "description" (tóm tắt dưới 50 từ). '
        'Ví dụ: {"chapters": [{"chapter": 1, "items": [{"entity_name": "A", "type": "CHARACTER", "description": "..."}]}, {"chapter": 2, "items": []}]}. Chỉ trả về JSON.'
    )
    full_prompt = "\n".join(prompt_parts)
    out = {ch_num: [] for ch_num, _ in contents_list}
    try:
        resp = AIService.call_openrouter(
            messages=[{"role": "user", "content": full_prompt}],
            model=_get_default_tool_model(),
            temperature=0.0,
            max_tokens=16000,
            response_format={"type": "json_object"},
        )
        if not resp or not resp.choices:
            return out
        raw = resp.choices[0].message.content.strip()
        obj = json.loads(AIService.clean_json_text(raw))
        chapters = obj.get("chapters") if isinstance(obj, dict) else []
        for block in chapters or []:
            ch = block.get("chapter")
            items = block.get("items")
            if ch is not None and isinstance(items, list):
                out[int(ch)] = items
    except Exception:
        pass
    return out


def render_data_analyze_tab(project_id):
    if not project_id:
        st.info("📁 Vui lòng chọn Project ở thanh bên trái.")
        return

    st.session_state.setdefault("update_trigger", 0)
    file_list = get_chapters_cached(project_id, st.session_state.get("update_trigger", 0))
    file_options = {}
    for f in file_list:
        display_name = f"📄 #{f['chapter_number']}: {f.get('title') or f'Chapter {f['chapter_number']}'}"
        file_options[display_name] = f["chapter_number"]

    if not file_list:
        st.info("Chưa có chương nào. Tạo chương trong Workstation trước.")
        return

    services = init_services()
    if not services:
        st.warning("Không kết nối được dịch vụ.")
        return
    supabase = services["supabase"]

    selected_file = st.selectbox(
        "Chọn chương để phân tích",
        list(file_options.keys()),
        key="da_chapter_select",
    )
    chap_num = file_options.get(selected_file, 1)
    _trigger = st.session_state.get("update_trigger", 0)
    selected_row = get_chapter_content_cached(project_id, chap_num, _trigger)
    content = (selected_row.get("content") or "").strip() if selected_row else ""

    if not content:
        st.warning("Chương này chưa có nội dung. Thêm nội dung trong Workstation.")
        st.stop()

    st.caption(f"Nội dung chương: {len(content)} ký tự.")

    _render_extract_bible_relations_chunking(
        project_id, content, chap_num, selected_row, file_options, selected_file, supabase
    )

    st.session_state.setdefault("update_trigger", st.session_state.get("update_trigger", 0))


def _render_extract_bible_relations_chunking(project_id, content, chap_num, selected_row, file_options, selected_file, supabase):
    """Chỉ Unified: 1 lần LLM → Bible + Timeline + Chunks + Relations + link 1-N. Đồng bộ toàn cục khi cần."""
    uid = getattr(st.session_state.get("user"), "id", None) or ""
    uem = getattr(st.session_state.get("user"), "email", None) or ""
    can_write = check_permission(uid, uem, project_id, "write")

    st.markdown("---")
    st.subheader("📥 Phân tích chương (Unified)")
    st.caption("**Đồng bộ toàn cục:** Kiểm tra và sửa link rác (chunk_bible, chunk_timeline), relation rác, parent_id Bible, source_chapter. Chạy khi bấm, trong background.")
    if can_write and st.button("🔄 Đồng bộ dữ liệu toàn cục", type="secondary", key="da_global_sync_btn"):
        label = "Đồng bộ dữ liệu toàn cục"
        job_id = create_job(
            story_id=project_id,
            user_id=uid or None,
            job_type="global_data_sync",
            label=label,
            payload={},
            post_to_chat=True,
        )
        if job_id:
            ensure_background_job_runner()
            st.toast("Đã xếp hàng. Xem tab Background Jobs hoặc chat.")
            st.session_state["update_trigger"] = st.session_state.get("update_trigger", 0) + 1
        else:
            st.error("Không tạo được job.")
    st.caption("**Unified:** 1 lần LLM → Bible + Timeline + Chunks + Relations + link 1-N. Có retry từng bước; thất bại thì rollback. Bible và Chunk có embedding.")
    st.checkbox(
        "⚠️ Tôi hiểu: Phân tích sẽ **xóa toàn bộ** Bible, Timeline, Chunks, Relations và link của chương này trước khi chạy.",
        key="da_confirm_delete_bible_chapter",
    )
    if st.session_state.get("da_confirm_delete_bible_chapter") and can_write:
        if st.button("▶️ Unified analyze chương này", type="primary", key="da_unified_btn"):
            label = f"Unified chương {chap_num}"
            job_id = create_job(
                story_id=project_id,
                user_id=uid or None,
                job_type="unified_chapter_analyze",
                label=label,
                payload={"chapter_number": chap_num},
                post_to_chat=False,
            )
            if job_id:
                ensure_background_job_runner()
                st.toast("Đã xếp hàng. Xem tab Background Jobs.")
                st.session_state["update_trigger"] = st.session_state.get("update_trigger", 0) + 1
            else:
                st.error("Không tạo được job.")
    st.markdown("---")
    st.subheader("📥 Unified nhiều chương (range)")
    st.caption("Chạy Unified liên tiếp cho nhiều chương (xóa & tạo lại Bible/Timeline/Chunks/Relations cho từng chương trong khoảng).")
    st.caption("Lưu ý: Khoảng chương này chỉ áp dụng cho Unified; các nút phân tích thành phần bên dưới vẫn dùng CHƯƠNG đang chọn ở trên.")
    if can_write:
        # Xác định khoảng chương khả dụng từ file_options
        try:
            chapter_numbers = sorted(set(int(v) for v in file_options.values()))
        except Exception:
            chapter_numbers = []
        if chapter_numbers:
            min_ch = chapter_numbers[0]
            max_ch = chapter_numbers[-1]
            default_range = (min_ch, max_ch)
            ch_start, ch_end = st.slider(
                "Khoảng chương Unified",
                min_value=min_ch,
                max_value=max_ch,
                value=default_range,
                key="da_unified_range_slider",
            )
            st.checkbox(
                f"⚠️ Tôi hiểu: Unified sẽ **xóa toàn bộ** Bible, Timeline, Chunks, Relations và link của CÁC CHƯƠNG từ {ch_start} đến {ch_end} trước khi chạy.",
                key="da_confirm_delete_bible_range",
            )
            if (
                st.session_state.get("da_confirm_delete_bible_range")
                and st.button(f"▶️ Unified analyze chương {ch_start}–{ch_end}", type="primary", key="da_unified_range_btn")
            ):
                s, e = sorted([int(ch_start), int(ch_end)])
                created = 0
                for ch in range(s, e + 1):
                    label_ch = f"Unified chương {ch}"
                    job_id = create_job(
                        story_id=project_id,
                        user_id=uid or None,
                        job_type="unified_chapter_analyze",
                        label=label_ch,
                        payload={"chapter_number": ch},
                        post_to_chat=False,
                    )
                    if job_id:
                        created += 1
                if created > 0:
                    ensure_background_job_runner()
                    st.toast(f"Đã xếp hàng Unified cho {created} chương. Xem tab Background Jobs.")
                    st.session_state["update_trigger"] = st.session_state.get("update_trigger", 0) + 1
                else:
                    st.error("Không tạo được job nào trong khoảng chương đã chọn.")
        else:
            st.info("Không xác định được danh sách chương để chạy Unified nhiều chương.")

    st.markdown("---")
    st.subheader("➕ Thêm trên nền có sẵn (không xóa)")
    st.caption("Trích xuất từ chương đã chọn và **thêm** vào dữ liệu hiện có (không xóa Bible/Timeline/Chunks/Relations của chương). Khác với Unified là xóa rồi làm lại.")
    if can_write:
        col_b, col_r, col_t, col_c = st.columns(4)
        with col_b:
            if st.button("📚 Thêm Bible từ chương", key="da_add_bible_btn"):
                job_id = create_job(
                    story_id=project_id,
                    user_id=uid or None,
                    job_type="data_analyze_bible",
                    label=f"Thêm Bible chương {chap_num}",
                    payload={"chapter_number": chap_num, "exclude_existing": True},
                    post_to_chat=False,
                )
                if job_id:
                    ensure_background_job_runner()
                    st.toast("Đã xếp hàng. Xem tab Background Jobs.")
                    st.session_state["update_trigger"] = st.session_state.get("update_trigger", 0) + 1
        with col_r:
            if st.button("🔗 Thêm Relation từ chương", key="da_add_relation_btn"):
                job_id = create_job(
                    story_id=project_id,
                    user_id=uid or None,
                    job_type="data_analyze_relation",
                    label=f"Thêm Relation chương {chap_num}",
                    payload={"chapter_number": chap_num, "only_new": True},
                    post_to_chat=False,
                )
                if job_id:
                    ensure_background_job_runner()
                    st.toast("Đã xếp hàng. Xem tab Background Jobs.")
                    st.session_state["update_trigger"] = st.session_state.get("update_trigger", 0) + 1
        with col_t:
            if st.button("📅 Thêm Timeline từ chương", key="da_add_timeline_btn"):
                job_id = create_job(
                    story_id=project_id,
                    user_id=uid or None,
                    job_type="data_analyze_timeline",
                    label=f"Thêm Timeline chương {chap_num}",
                    payload={"chapter_number": chap_num, "append_only": True},
                    post_to_chat=False,
                )
                if job_id:
                    ensure_background_job_runner()
                    st.toast("Đã xếp hàng. Xem tab Background Jobs.")
                    st.session_state["update_trigger"] = st.session_state.get("update_trigger", 0) + 1
        with col_c:
            if st.button("✂️ Thêm Chunk từ chương", key="da_add_chunk_btn"):
                job_id = create_job(
                    story_id=project_id,
                    user_id=uid or None,
                    job_type="data_analyze_chunk",
                    label=f"Thêm Chunk chương {chap_num}",
                    payload={"chapter_number": chap_num, "append_only": True},
                    post_to_chat=False,
                )
                if job_id:
                    ensure_background_job_runner()
                    st.toast("Đã xếp hàng. Xem tab Background Jobs.")
                    st.session_state["update_trigger"] = st.session_state.get("update_trigger", 0) + 1
    if not can_write:
        st.warning("Chỉ thành viên có quyền ghi mới được thực hiện.")
