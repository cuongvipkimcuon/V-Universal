# views/python_executor_view.py - UI Python Executor (ưu tiên tính toán số liệu)
"""Giao diện Python Executor - chạy code Pandas/NumPy khi user hỏi về số liệu."""
import re
import streamlit as st
import json

from config import Config, init_services
from ai_engine import AIService, ContextManager, check_semantic_intent, SmartAIRouter, _get_default_tool_model
from utils.python_executor import PythonExecutor
from utils.auth_manager import check_permission
from persona import PersonaSystem


def _run_at_at_mode(question: str, project_id: str) -> tuple:
    """
    Xử lý câu hỏi @@ như numerical_calculation. Trả về (result_str, error_msg).
    Nếu thành công: (str(result), None). Nếu lỗi/không tính được: (None, "Không thể tính toán" hoặc chi tiết).
    Không lưu vào chat.
    """
    if not question or not str(question).strip():
        return None, "Không thể tính toán"
    q = str(question).strip()
    persona = PersonaSystem.get_persona("Writer")
    router_out = {"intent": "numerical_calculation", "target_files": [], "target_bible_entities": [], "rewritten_query": q, "chapter_range": None, "chapter_range_mode": None, "chapter_range_count": 5}
    semantic = check_semantic_intent(q, project_id)
    if semantic and semantic.get("intent") == "numerical_calculation" and semantic.get("related_data"):
        router_out["_semantic_data"] = semantic["related_data"]
    try:
        context_text, sources, _, _ = ContextManager.build_context(
            router_out, project_id, persona, False,
            current_arc_id=st.session_state.get("current_arc_id"),
            session_state=dict(st.session_state),
        )
        if router_out.get("_semantic_data"):
            context_text = f"[SEMANTIC - Data]\n{router_out['_semantic_data']}\n\n{context_text}"
        if not context_text or len(context_text.strip()) < 50:
            return None, "Không thể tính toán (không có dữ liệu phù hợp)"
        code_prompt = f"""User hỏi: "{q}"
Context có sẵn:
{context_text[:6000]}

Nhiệm vụ: Tạo code Python (pandas/numpy) để trả lời. Gán kết quả cuối vào biến result.
Chỉ trả về code trong block ```python ... ```, không giải thích."""
        code_resp = AIService.call_openrouter(
            messages=[{"role": "user", "content": code_prompt}],
            model=_get_default_tool_model(),
            temperature=0.1,
            max_tokens=2000,
        )
        raw = (code_resp.choices[0].message.content or "").strip()
        m = re.search(r"```(?:python)?\s*(.*?)```", raw, re.DOTALL)
        code = (m.group(1).strip() if m else raw).strip()
        if not code:
            return None, "Không thể tính toán"
        val, err = PythonExecutor.execute(code, result_variable="result")
        if err:
            return None, "Không thể tính toán"
        return str(val) if val is not None else "null", None
    except Exception as ex:
        return None, "Không thể tính toán"


def render_python_executor_tab(project_id):
    """Tab Python Executor - chạy code tính toán, ưu tiên dùng khi user hỏi về số."""
    st.subheader("🧮 Python Executor (Calculator)")
    st.caption("Ưu tiên dùng khi Chat hỏi về số liệu, thống kê, tính toán. Hệ thống tự gọi khi intent = numerical_calculation.")

    if not project_id:
        st.info("📁 Chọn Project để bắt đầu.")
        return

    user = st.session_state.get("user")
    user_id = getattr(user, "id", None) if user else None
    user_email = getattr(user, "email", None) if user else None
    can_write = bool(
        project_id and user_id
        and check_permission(str(user_id), user_email or "", project_id, "write")
    )

    with st.expander("📖 Hướng dẫn", expanded=False):
        st.markdown("""
        - **Mục đích**: Chạy code Python (Pandas, NumPy) trong sandbox an toàn.
        - **@@**: Nhập `@@câu hỏi` để tính toán từ dữ liệu project (intent = numerical_calculation). Không lưu chat.
        - **Thủ công**: Dán code ở đây để test hoặc chạy trực tiếp.
        """)

    at_at_input = st.text_input(
        "@@ Câu hỏi tính toán (từ dữ liệu project, không lưu chat)",
        placeholder="VD: @@ Tổng cột A trong Excel là bao nhiêu?",
        key="py_exec_at_at",
    )
    if at_at_input and at_at_input.strip().startswith("@@"):
        q = at_at_input.strip()[2:].strip()
        if q and st.button("▶️ Chạy @@", key="py_exec_at_at_run"):
            with st.spinner("Đang tính toán..."):
                result, err = _run_at_at_mode(q, project_id)
                if err:
                    st.error(err)
                else:
                    st.success("Kết quả:")
                    try:
                        s = result.strip() if result else ""
                        if s and (s.startswith("{") or s.startswith("[")):
                            st.json(json.loads(result))
                        else:
                            st.metric("Kết quả", result)
                    except Exception:
                        st.write(result)
    st.markdown("---")

    code_input = st.text_area(
        "Code Python (phải gán kết quả vào biến `result`)",
        value="""import pandas as pd
# Ví dụ: tính tổng cột
df = pd.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6]})
result = df["a"].sum()""",
        height=150,
        key="python_exec_code",
        help="Dùng pd (pandas), np (numpy), math. Gán kết quả cuối vào biến result."
    )

    col_run, col_clear = st.columns([1, 4])
    with col_run:
        if st.button("▶️ Chạy", type="primary", key="py_exec_run"):
            if code_input and code_input.strip():
                with st.spinner("Đang chạy..."):
                    val, err = PythonExecutor.execute(code_input.strip(), result_variable="result")
                    if err:
                        st.error(f"Lỗi: {err}")
                    else:
                        st.success("Thành công!")
                        try:
                            if isinstance(val, (dict, list)):
                                st.json(val)
                            elif hasattr(val, "to_dict"):
                                st.json(val.to_dict())
                            elif hasattr(val, "tolist"):
                                st.write(val.tolist())
                            else:
                                st.metric("Kết quả", str(val))
                        except Exception:
                            st.write(f"`{val}`")
            else:
                st.warning("Nhập code trước khi chạy.")

    st.markdown("---")
    st.caption("💡 Trong Chat, khi bạn hỏi ví dụ: 'Tổng cột A trong file Excel là bao nhiêu?', AI sẽ tự động sinh code và dùng Executor để trả số chính xác.")
