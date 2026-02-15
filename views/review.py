# views/review.py - Tab Review: chọn chương, persona, gọi AI review, lưu/xóa review (không tự động lưu)
# Dùng build_logic_context_for_chapter (5 dimensions: timeline, bible, relation, chat_crystallize, rule) từ core.chapter_logic_check.
import streamlit as st

from config import Config, init_services
from ai_engine import AIService
from utils.cache_helpers import get_chapters_cached, invalidate_cache
from persona import PersonaSystem
from core.chapter_logic_check import build_logic_context_for_chapter


def render_review_tab(project_id, persona=None):
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
        "Chọn chương để review",
        list(file_options.keys()),
        key="review_chapter_select",
    )
    chap_num = file_options.get(selected_file, 1)
    res = (
        supabase.table("chapters")
        .select("id, content, title, review_content, arc_id")
        .eq("story_id", project_id)
        .eq("chapter_number", chap_num)
        .limit(1)
        .execute()
    )
    selected_row = res.data[0] if res.data and len(res.data) > 0 else None
    content = (selected_row.get("content") or "").strip() if selected_row else ""
    db_review = (selected_row.get("review_content") or "").strip() if selected_row else ""
    chapter_arc_id = selected_row.get("arc_id") if selected_row else None

    # Persona cho review
    personas_avail = PersonaSystem.get_available_personas()
    review_persona_key = st.selectbox(
        "🎭 Persona cho Review",
        personas_avail,
        key="review_persona_select",
    )
    review_persona = PersonaSystem.get_persona(review_persona_key)
    review_prompt_template = review_persona.get("review_prompt") or "Đánh giá nội dung sau theo góc nhìn chuyên môn. Nêu điểm mạnh, điểm yếu và gợi ý cải thiện."

    # Unsaved review (sau khi bấm "Review" AI, chưa lưu DB)
    unsaved = st.session_state.get("review_unsaved")
    unsaved_chap = st.session_state.get("review_unsaved_chap")
    has_unsaved_for_this = unsaved is not None and unsaved_chap == chap_num

    # Chỉ hiện nội dung review khi: đã có trong DB hoặc có bản unsaved cho chương này
    show_review_block = bool(db_review) or has_unsaved_for_this
    current_display = unsaved if has_unsaved_for_this else db_review

    if not content:
        st.warning("Chương này chưa có nội dung. Thêm nội dung trong Workstation trước khi review.")
        st.stop()

    st.caption("Review không tự động lưu — bấm **Lưu review hiện tại** để ghi vào database.")

    # --- Khối nội dung review (hiện trước để khi bấm Lưu ta có giá trị widget) ---
    current_review_text = None
    if show_review_block:
        st.markdown("---")
        st.subheader("Nội dung review")
        current_review_text = st.text_area(
            "Chỉnh sửa review (bấm **Lưu review hiện tại** để ghi vào database)",
            value=current_display,
            height=400,
            key=f"review_edit_{chap_num}",
            label_visibility="collapsed",
        )

    # --- Nút hành động ---
    st.markdown("---")
    col_review_btn, col_save, col_del = st.columns([1, 1, 1])

    with col_review_btn:
        if st.button("🤖 Review (gọi AI)", type="primary", key="review_ai_btn", use_container_width=True):
            with st.spinner("Đang gọi AI review..."):
                chapter_id = selected_row.get("id") if selected_row else None
                logic_context = build_logic_context_for_chapter(
                    project_id,
                    chapter_id or 0,
                    chap_num,
                    arc_id=chapter_arc_id,
                    include_archived=False,
                ) if chapter_id else "(Chưa có chương.)"
                prompt = f"""{review_prompt_template}

---
DỮ LIỆU THAM CHIẾU (Timeline, Bible, Relation, Chat crystallize, Rule — dùng để soi lỗi logic):
---
{logic_context}

---
⚠️ YÊU CẦU THÊM: Kiểm tra xem nội dung chương mới có mâu thuẫn logic với dữ liệu trên không (timeline, nhân vật, quan hệ, điểm nhớ chat, quy tắc). Nếu có sai lệch hoặc plot hole so với nội dung cũ, nêu rõ trong phần review.
---
NỘI DUNG CHƯƠNG CẦN REVIEW:
---
{content[:120000]}
"""
                try:
                    response = AIService.call_openrouter(
                        messages=[{"role": "user", "content": prompt}],
                        model=st.session_state.get("selected_model", Config.DEFAULT_MODEL),
                        temperature=review_persona.get("temperature", 0.7),
                        max_tokens=int(review_persona.get("max_tokens", 5000)),
                    )
                    if response and response.choices:
                        text = response.choices[0].message.content.strip()
                        st.session_state["review_unsaved"] = text
                        st.session_state["review_unsaved_chap"] = chap_num
                        st.success("Đã tạo review. Chỉnh sửa nếu cần rồi bấm **Lưu review hiện tại**.")
                    else:
                        st.error("AI không trả về nội dung.")
                except Exception as e:
                    st.error(f"Lỗi gọi AI: {e}")

    with col_save:
        save_clicked = st.button("💾 Lưu review hiện tại", key="review_save_btn", use_container_width=True, disabled=not show_review_block)
    with col_del:
        if st.button("🗑️ Xóa review (trong DB)", key="review_del_btn", use_container_width=True):
            supabase.table("chapters").update({"review_content": ""}).eq("story_id", project_id).eq("chapter_number", chap_num).execute()
            st.session_state.pop("review_unsaved", None)
            st.session_state.pop("review_unsaved_chap", None)
            st.session_state["update_trigger"] = st.session_state.get("update_trigger", 0) + 1
            st.success("Đã xóa review khỏi database. Bấm Refresh để cập nhật.")

    # Lưu: chỉ khi đã có khối review và có nội dung từ widget
    if save_clicked and show_review_block:
        to_save = current_review_text if current_review_text is not None else st.session_state.get(f"review_edit_{chap_num}", "")
        supabase.table("chapters").update({"review_content": to_save}).eq("story_id", project_id).eq("chapter_number", chap_num).execute()
        st.session_state.pop("review_unsaved", None)
        st.session_state.pop("review_unsaved_chap", None)
        st.session_state["update_trigger"] = st.session_state.get("update_trigger", 0) + 1
        invalidate_cache()
        st.success("Đã lưu review. Bấm Refresh để cập nhật.")
