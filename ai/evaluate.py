# ai/evaluate.py - V7 dynamic re-planning: evaluate_step_outcome, replan_after_step
import json
from typing import Dict, List, Optional, Tuple

from ai.service import AIService, _get_default_tool_model


def evaluate_step_outcome(intent: str, ctx_text: str, sources: List[str]) -> Tuple[bool, str]:
    """
    Đánh giá bước vừa chạy: có "thất bại" (không tìm thấy dữ liệu) cần cân nhắc re-plan không.
    Returns: (should_consider_replan, reason).
    """
    if not intent or intent in ("chat_casual", "ask_user_clarification", "update_data", "web_search"):
        return False, ""
    ctx_upper = (ctx_text or "").upper()
    src_list = sources or []

    if intent == "read_full_content":
        if "--- TARGET CONTENT ---" not in ctx_text and "NỘI DUNG CHƯƠNG" not in ctx_text:
            return True, "read_full_content: không tìm thấy file/chương (target content trống)"
        return False, ""

    if intent == "search_chunks":
        has_chunk = any("chunk" in s.lower() or "reverse" in s.lower() for s in src_list)
        has_fallback = "Chapter fallback" in str(src_list) or "NỘI DUNG CHƯƠNG" in ctx_text
        if not has_chunk and not has_fallback:
            return True, "search_chunks: không tìm thấy chunk hoặc fallback chương"
        return False, ""

    if intent == "search_bible":
        has_bible = "📚" in str(src_list) or "KNOWLEDGE BASE" in ctx_upper or ("--- " in ctx_text and "---" in ctx_text)
        if not has_bible or (len(ctx_text or "") < 500 and "Bible" not in ctx_text):
            return True, "search_bible: không tìm thấy dữ liệu Bible"
        return False, ""

    if intent == "mixed_context":
        has_any = "📚" in str(src_list) or "RELATED FILES" in ctx_text or "Timeline" in ctx_upper or "Chunk" in str(src_list)
        if not has_any:
            return True, "mixed_context: không có Bible, file, timeline hay chunk"
        return False, ""

    if intent == "manage_timeline":
        if "[TIMELINE] Chưa có dữ liệu" in ctx_text or "Timeline (empty)" in str(src_list):
            return True, "manage_timeline: chưa có dữ liệu timeline_events"
        return False, ""

    if intent == "query_Sql":
        if "KNOWLEDGE BASE (query_Sql" not in ctx_text and "🔍 Query SQL" not in str(src_list):
            return True, "query_Sql: không có dữ liệu Bible/đối tượng"
        return False, ""

    return False, ""


def replan_after_step(
    user_prompt: str,
    cumulative_context: str,
    step_results: List[Dict],
    step_just_done: Dict,
    outcome_reason: str,
    remaining_plan: List[Dict],
    project_id: Optional[str] = None,
) -> Tuple[str, str, List[Dict]]:
    """
    Gọi LLM quyết định: continue / replace / abort sau khi một bước thất bại (không tìm thấy dữ liệu).
    Returns: (action, reason, new_plan). new_plan chỉ có khi action == "replace".
    """
    intent_done = step_just_done.get("intent", "chat_casual")
    args_done = step_just_done.get("args") or {}
    remaining_summary = json.dumps([{"step_id": s.get("step_id"), "intent": s.get("intent")} for s in remaining_plan], ensure_ascii=False)

    prompt_text = f"""User hỏi: "{user_prompt[:500]}"

Vừa thực thi xong bước: intent={intent_done}, args={json.dumps(args_done, ensure_ascii=False)[:300]}.
Kết quả bước này: {outcome_reason} (không tìm thấy dữ liệu / thất bại).

Context đã tích lũy (rút gọn): {cumulative_context[:2500]}...

Kế hoạch còn lại (chưa chạy): {remaining_summary}

Nhiệm vụ: Quyết định một trong ba:
1. **continue** – Giữ nguyên plan còn lại, chạy tiếp (thử bước tiếp theo).
2. **replace** – Thay thế plan còn lại bằng plan mới (vd: thay "tìm file A" bằng "tìm file B", hoặc đổi intent khác phù hợp). Trả về new_plan là mảng bước thay thế (format giống plan: step_id, intent, args với query_refined, target_files, target_bible_entities, chapter_range, ...).
3. **abort** – Dừng thực thi, không chạy thêm bước; trả lời dựa trên context hiện có.

Trả về ĐÚNG MỘT JSON (chỉ JSON, không giải thích):
{{ "action": "continue" | "replace" | "abort", "reason": "Lý do ngắn", "new_plan": [] }}

Với action=replace thì new_plan phải có ít nhất 1 bước. Với continue/abort thì new_plan để []."""

    try:
        r = AIService.call_openrouter(
            messages=[
                {"role": "system", "content": "Bạn là V7 Re-planner. Chỉ trả về JSON với action, reason, new_plan."},
                {"role": "user", "content": prompt_text},
            ],
            model=_get_default_tool_model(),
            temperature=0.2,
            max_tokens=600,
            response_format={"type": "json_object"},
        )
        content = AIService.clean_json_text(r.choices[0].message.content or "{}")
        data = json.loads(content)
        action = (data.get("action") or "continue").strip().lower()
        if action not in ("continue", "replace", "abort"):
            action = "continue"
        reason = str(data.get("reason") or "").strip() or outcome_reason
        new_plan = data.get("new_plan") if isinstance(data.get("new_plan"), list) else []
        if action == "replace" and not new_plan:
            action = "continue"
            new_plan = []
        return action, reason, new_plan
    except Exception as e:
        print(f"replan_after_step error: {e}")
        return "continue", "", []
