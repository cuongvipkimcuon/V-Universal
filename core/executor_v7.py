# core/executor_v7.py - V7 Sequential Execution Engine + Dynamic Re-planning
"""Thực thi plan tuần tự; sau mỗi bước đánh giá outcome và có thể re-plan (thay bước còn lại)."""
from typing import Dict, List, Tuple, Any, Optional

# Import từ ai_engine khi cần (tránh circular)
def _get_engine():
    from ai_engine import ContextManager, AIService, parse_chapter_range_from_query, _get_default_tool_model
    from config import Config
    return ContextManager, AIService, Config, parse_chapter_range_from_query, _get_default_tool_model


def _get_replan():
    from ai_engine import evaluate_step_outcome, replan_after_step
    return evaluate_step_outcome, replan_after_step


def step_to_router_result(step: Dict, user_prompt: str) -> Dict:
    """Chuyển một step trong plan thành dict router_result cho ContextManager.build_context."""
    intent = step.get("intent", "chat_casual")
    args = step.get("args") or {}
    return {
        "intent": intent,
        "context_needs": args.get("context_needs") if isinstance(args.get("context_needs"), list) else [],
        "context_priority": args.get("context_priority") if isinstance(args.get("context_priority"), list) else [],
        "target_files": args.get("target_files") or [],
        "target_bible_entities": args.get("target_bible_entities") or [],
        "rewritten_query": args.get("query_refined") or user_prompt,
        "chapter_range": args.get("chapter_range"),
        "chapter_range_mode": args.get("chapter_range_mode"),
        "chapter_range_count": args.get("chapter_range_count", 5),
        "inferred_prefixes": args.get("inferred_prefixes") or [],
        "clarification_question": args.get("clarification_question") or "",
        "update_summary": args.get("update_summary") or "",
        "data_operation_type": args.get("data_operation_type") or "",
        "data_operation_target": args.get("data_operation_target") or "",
        "query_target": args.get("query_target") or "",
    }


def _build_router_result_with_router_3step_for_search(
    step: Dict,
    user_prompt: str,
    project_id: str,
    chat_history_text: str = "",
) -> Dict:
    """
    Dùng lại Context Planner cho intent search_context trong V7 (giảm tải intent_only):
    1) context_planner (quyết định context_needs, targets... từ query_refined + overview DB)
    2) Áp dụng override từ plan (chapter_range, context_needs, targets) để giữ ý định của B1.
    """
    try:
        from ai.router import SmartAIRouter
    except Exception:
        # Fallback: dùng schema từ plan như cũ nếu không import được Router.
        return step_to_router_result(step, user_prompt)

    args = step.get("args") or {}
    query_refined = args.get("query_refined") or user_prompt
    # Chỉ dùng context_planner (bỏ intent_only để giảm 1 lần gọi LLM/step).
    ctx_router = SmartAIRouter.context_planner(
        query_refined,
        "search_context",
        chat_history_text,
        project_id,
        relevant_rules="",
    )
    # Override bằng thông tin chi tiết từ plan (nếu có) để "ưu tiên ý" của B1.
    # 1) context_needs / context_priority
    plan_needs = args.get("context_needs")
    if isinstance(plan_needs, list) and plan_needs:
        ctx_router["context_needs"] = plan_needs
    plan_priority = args.get("context_priority")
    if isinstance(plan_priority, list) and plan_priority:
        ctx_router["context_priority"] = plan_priority
    # 2) chapter_range + mode + count
    if "chapter_range" in args and args.get("chapter_range") is not None:
        ctx_router["chapter_range"] = args.get("chapter_range")
        if args.get("chapter_range_mode") is not None:
            ctx_router["chapter_range_mode"] = args.get("chapter_range_mode")
        if args.get("chapter_range_count") is not None:
            ctx_router["chapter_range_count"] = args.get("chapter_range_count")
    # 3) Các target chi tiết (nếu B1 đã sinh): ưu tiên target từ plan khi có.
    for key in (
        "target_bible_entities",
        "target_chunk_keywords",
        "target_relation_entities",
        "target_timeline_keywords",
        "inferred_prefixes",
        "query_target",
        "data_operation_type",
        "data_operation_target",
    ):
        val = args.get(key)
        if isinstance(val, list):
            if val:
                ctx_router[key] = val
        elif val:
            ctx_router[key] = val
    # 4) Gắn lại rewritten_query = query_refined để ContextManager dùng đúng câu step này.
    ctx_router["rewritten_query"] = query_refined
    # V7: search_context luôn theo kiến trúc V8 — full 4 nguồn + chapter, ưu tiên chunk, bible, relation, timeline.
    try:
        from ai.context_schema import FULL_CONTEXT_NEEDS_V8
        ctx_router["context_needs"] = list(FULL_CONTEXT_NEEDS_V8)
        ctx_router["context_priority"] = ["chunk", "bible", "relation", "timeline", "chapter"]
    except Exception:
        pass
    return ctx_router


def _normalize_step(step: Dict, step_id: int, user_prompt: str) -> Dict:
    """Chuẩn hóa step từ LLM re-plan (có thể thiếu key) thành format đủ args."""
    args = step.get("args") if isinstance(step.get("args"), dict) else {}
    return {
        "step_id": step_id,
        "intent": (step.get("intent") or "chat_casual").strip(),
        "args": {
            "query_refined": args.get("query_refined") or args.get("rewritten_query") or user_prompt,
            "context_needs": args.get("context_needs") if isinstance(args.get("context_needs"), list) else [],
            "context_priority": args.get("context_priority") if isinstance(args.get("context_priority"), list) else [],
            "target_files": args.get("target_files") if isinstance(args.get("target_files"), list) else [],
            "target_bible_entities": args.get("target_bible_entities") if isinstance(args.get("target_bible_entities"), list) else [],
            "chapter_range": args.get("chapter_range"),
            "chapter_range_mode": args.get("chapter_range_mode"),
            "chapter_range_count": args.get("chapter_range_count", 5),
            "inferred_prefixes": args.get("inferred_prefixes") if isinstance(args.get("inferred_prefixes"), list) else [],
            "clarification_question": args.get("clarification_question") or "",
            "update_summary": args.get("update_summary") or "",
            "data_operation_type": args.get("data_operation_type") or "",
            "data_operation_target": args.get("data_operation_target") or "",
            "query_target": args.get("query_target") or "",
        },
        "dependency": step.get("dependency"),
    }


def execute_plan(
    plan: List[Dict],
    project_id: str,
    persona: Dict,
    user_prompt: str,
    strict_mode: bool = False,
    current_arc_id: Optional[str] = None,
    session_state: Optional[Dict] = None,
    free_chat_mode: bool = False,
    max_context_tokens: Optional[int] = None,
    run_numerical_executor: bool = True,
    max_steps_per_turn: int = 10,
    max_replan_rounds: int = 2,
    llm_budget_ref: Optional[List[int]] = None,
    max_distinct_intents: int = 3,
    max_retries_per_intent: int = 1,
) -> Tuple[str, List[str], List[Dict], List[Dict], List[Dict]]:
    """
    Thực thi plan; sau mỗi bước có thể re-plan (đổi phần còn lại nếu bước vừa thất bại).
    llm_budget_ref: [current_count, max_count] — numerical LLM chỉ gọi khi current < max; gọi xong tăng current. None = không giới hạn.
    Returns: (cumulative_context, sources, step_results, replan_events, data_operation_steps).
    data_operation_steps: các bước unified cần chạy job sau.
    """
    ContextManager, AIService, Config, parse_chapter_range_from_query, _get_default_tool_model = _get_engine()
    try:
        from utils.python_executor import PythonExecutor
    except ImportError:
        PythonExecutor = None

    evaluate_step_outcome, replan_after_step = _get_replan()

    cumulative_parts: List[str] = []
    all_sources: List[str] = []
    step_results: List[Dict] = []
    replan_events: List[Dict] = []
    data_operation_steps: List[Dict] = []

    token_limit = max_context_tokens or Config.CONTEXT_SIZE_TOKENS.get("medium", 60000)
    remaining_steps = list(plan)
    steps_executed = 0
    replan_count = 0
    distinct_intents = set()
    retry_count_per_intent: Dict[str, int] = {}

    while remaining_steps and steps_executed < max_steps_per_turn:
        step = remaining_steps[0]
        step_id = step.get("step_id", len(step_results) + 1)
        intent = step.get("intent", "chat_casual")
        args = step.get("args") or {}
        op_target = (args.get("data_operation_target") or "").strip()

        # Giới hạn số loại intent khác nhau được thực thi trong một lượt.
        # Unified/update_data được coi là thao tác nền, không tính vào giới hạn này.
        if intent not in ("update_data", "unified"):
            if intent not in distinct_intents:
                if len(distinct_intents) >= max_distinct_intents:
                    skip_note = (
                        f"[SKIP] Bỏ qua STEP {step_id} (intent={intent}) "
                        f"vì đã đạt giới hạn {max_distinct_intents} loại tác vụ trong một lượt."
                    )
                    cumulative_parts.append(f"\n--- [STEP {step_id}: {intent}] ---\n{skip_note}\n")
                    step_results.append({
                        "step_id": step_id,
                        "intent": intent,
                        "context_snippet": "",
                        "executor_result": None,
                        "evaluation_status": "skipped",
                    })
                    break
                distinct_intents.add(intent)

        # Bước unified (hoặc legacy update_data): thu thập để chạy job unified_chapter_range sau, không build context.
        if intent in ("update_data", "unified") and op_target == "unified":
            op_type = args.get("data_operation_type") or "extract"
            ch_range = args.get("chapter_range")
            if ch_range and isinstance(ch_range, (list, tuple)) and len(ch_range) >= 2:
                try:
                    start, end = int(ch_range[0]), int(ch_range[1])
                    start, end = min(start, end), max(start, end)
                    data_operation_steps.append({"operation_type": op_type, "target": "unified", "chapter_range": [start, end]})
                except (ValueError, TypeError):
                    if ch_range and len(ch_range) >= 1:
                        ch_num = int(ch_range[0])
                        data_operation_steps.append({"operation_type": op_type, "target": "unified", "chapter_range": [ch_num, ch_num]})
            elif ch_range and len(ch_range) >= 1:
                ch_num = int(ch_range[0])
                data_operation_steps.append({"operation_type": op_type, "target": "unified", "chapter_range": [ch_num, ch_num]})
            block = f"\n--- [STEP {step_id}: unified] ---\n(Unified analyze chương — chờ xác nhận để thực hiện)\n"
            cumulative_parts.append(block)
            step_results.append({
                "step_id": step_id,
                "intent": intent,
                "context_snippet": "",
                "executor_result": None,
                "evaluation_status": "n/a",
            })
            steps_executed += 1
            remaining_steps = remaining_steps[1:]
            continue

        # Intent đặc biệt: multi_chapter_analysis (V7) — phân tích khoảng chương lớn bằng cách chia thành nhiều đoạn nhỏ.
        if intent == "multi_chapter_analysis":
            ch_range = args.get("chapter_range")
            start = end = None
            if isinstance(ch_range, (list, tuple)) and len(ch_range) >= 2:
                try:
                    start, end = int(ch_range[0]), int(ch_range[1])
                    start, end = min(start, end), max(start, end)
                except (ValueError, TypeError):
                    start = end = None
            # Nếu không xác định được khoảng chương, fallback về search_context thông thường.
            if start is None or end is None:
                intent = "search_context"
            else:
                total_chapters = end - start + 1
                # Chia thành các đoạn "gần đều", mặc định kích thước ~10 chương (ví dụ 1–30 -> 1–10,11–20,21–30).
                segment_size = 10 if total_chapters > 10 else total_chapters
                segments: List[Tuple[int, int]] = []
                cur = start
                while cur <= end:
                    seg_end = min(cur + segment_size - 1, end)
                    segments.append((cur, seg_end))
                    cur = seg_end + 1

                branch_blocks: List[str] = []
                all_branch_sources: List[str] = []

                # Gọi context_planner một lần cho toàn khoảng chương, rồi reuse cho các segment bằng cách override chapter_range.
                base_router_result = _build_router_result_with_router_3step_for_search(
                    step,
                    user_prompt,
                    project_id,
                    chat_history_text="",
                )

                for idx, (seg_start, seg_end) in enumerate(segments, 1):
                    # Copy từ router_result gốc, chỉ thay chapter_range cho sub-range; intent con là search_context.
                    seg_router_result = dict(base_router_result)
                    seg_router_result["intent"] = "search_context"
                    seg_router_result["chapter_range"] = [seg_start, seg_end]
                    seg_ctx_text, seg_sources, _, _ = ContextManager.build_context(
                        seg_router_result,
                        project_id,
                        persona,
                        strict_mode=strict_mode,
                        current_arc_id=current_arc_id,
                        session_state=session_state,
                        free_chat_mode=free_chat_mode,
                        max_context_tokens=token_limit,
                        for_v7_segment=True,
                    )

                    # Gọi LLM tool model cho từng đoạn, với retry tối đa 2 lần khi lỗi.
                    system_content = (
                        f"{persona.get('core_instruction', '')}\n\n"
                        f"- Bạn đang xử lý PHẦN {idx} trong khoảng chương {seg_start}-{seg_end}.\n"
                        f"- Chỉ phân tích và trả lời cho các chương trong khoảng {seg_start}-{seg_end}.\n"
                        f"- Kết quả sẽ được hệ thống gom lại để tạo câu trả lời cuối cùng."
                    )
                    user_content = (
                        f"CONTEXT (chỉ cho các chương {seg_start}-{seg_end}):\n{seg_ctx_text}\n\n"
                        f"YÊU CẦU GỐC CỦA USER:\n{user_prompt}\n\n"
                        f"Hãy chỉ phân tích phần câu hỏi này dựa trên các chương {seg_start}-{seg_end}, "
                        f"trả lời súc tích nhưng đủ ý; KHÔNG tổng kết cho toàn bộ {start}-{end} chương."
                    )
                    messages = [
                        {"role": "system", "content": system_content},
                        {"role": "user", "content": user_content},
                    ]

                    branch_answer = ""
                    last_error: Optional[Exception] = None
                    for attempt in range(3):
                        try:
                            model = _get_default_tool_model()
                            resp = AIService.call_openrouter(
                                messages=messages,
                                model=model,
                                temperature=0.1,
                                max_tokens=8000,
                            )
                            content = (resp.choices[0].message.content or "").strip()
                            if content:
                                branch_answer = content
                                if llm_budget_ref and len(llm_budget_ref) >= 1:
                                    llm_budget_ref[0] += 1
                                break
                        except Exception as ex:
                            last_error = ex
                            continue

                    if not branch_answer:
                        branch_answer = f"(Lỗi khi xử lý đoạn chương {seg_start}-{seg_end}: {last_error})"

                    block_hdr = f"#### PHÂN TÍCH ĐOẠN CHƯƠNG {seg_start}–{seg_end}\n"
                    branch_blocks.append(f"{block_hdr}\n{branch_answer}")
                    all_branch_sources.extend(seg_sources or [])

                # Gom tất cả phần trả lời nhánh thành context cho step này; final LLM (user model) sẽ tổng hợp lại.
                ctx_text = (
                    f"[MULTI-CHAPTER ANALYSIS] Khoảng chương {start}-{end}, chia thành {len(segments)} đoạn.\n\n"
                    + "\n\n".join(branch_blocks)
                )
                block = f"\n--- [STEP {step_id}: {intent}] ---\n{ctx_text}\n"
                cumulative_parts.append(block)
                all_sources.extend([f"Step {step_id}: {intent}"] + all_branch_sources)
                step_results.append({
                    "step_id": step_id,
                    "intent": intent,
                    "context_snippet": ctx_text[:2000],
                    "executor_result": None,
                })
                steps_executed += 1
                cumulative_so_far = "\n".join(cumulative_parts)
                remaining_after = remaining_steps[1:]

                # Vẫn cho phép re-plan dựa trên outcome tổng hợp của bước multi_chapter_analysis (nhưng các LLM call con không tạo thêm bước).
                if retry_count_per_intent.get(intent, 0) >= max_retries_per_intent:
                    should_replan, outcome_reason = (False, "")
                else:
                    should_replan, outcome_reason = evaluate_step_outcome(
                        intent,
                        ctx_text,
                        all_branch_sources or [],
                        step,
                    )
                evaluation_status = "ok"
                evaluation_reason = ""
                if should_replan:
                    evaluation_status = "failed"
                    evaluation_reason = outcome_reason
                # Ghi lại đánh giá cho step vừa chạy.
                if step_results:
                    step_results[-1]["evaluation_status"] = evaluation_status
                    if evaluation_reason:
                        step_results[-1]["evaluation_reason"] = evaluation_reason
                if should_replan and remaining_after and replan_count < max_replan_rounds:
                    action, reason, new_plan = replan_after_step(
                        user_prompt,
                        cumulative_so_far,
                        step_results,
                        step,
                        outcome_reason,
                        remaining_after,
                        project_id,
                    )
                    replan_events.append({
                        "step_id": step_id,
                        "reason": reason or outcome_reason,
                        "action": action,
                        "new_plan_summary": [s.get("intent") for s in new_plan] if new_plan else [],
                    })
                    if action == "replace" and new_plan:
                        replan_count += 1
                        retry_count_per_intent[intent] = retry_count_per_intent.get(intent, 0) + 1
                        normalized = []
                        for i, s in enumerate(new_plan):
                            normalized.append(_normalize_step(s, len(step_results) + 1 + i, user_prompt))
                        remaining_steps = normalized
                        continue
                    if action == "abort":
                        remaining_steps = []
                        break
                remaining_steps = remaining_after
                continue

        # Các intent còn lại: build context chuẩn rồi để final LLM (user model) dùng.
        if intent == "search_context":
            router_result = _build_router_result_with_router_3step_for_search(
                step,
                user_prompt,
                project_id,
                chat_history_text="",
            )
        else:
            router_result = step_to_router_result(step, user_prompt)

        ctx_text, sources, _, _ = ContextManager.build_context(
            router_result,
            project_id,
            persona,
            strict_mode=strict_mode,
            current_arc_id=current_arc_id,
            session_state=session_state,
            free_chat_mode=free_chat_mode,
            max_context_tokens=token_limit,
        )

        # Intent không sinh "nguyên liệu" cho bước sau: chỉ ghi nhắc ngắn, không đưa full context vào cumulative.
        INDEPENDENT_INTENTS = ("query_Sql", "web_search", "ask_user_clarification", "chat_casual")
        if intent in INDEPENDENT_INTENTS:
            block = f"\n--- [STEP {step_id}: {intent}] ---\n(Đã thực hiện; bước sau không dùng kết quả này làm nguồn.)\n"
            cumulative_parts.append(block)
            all_sources.extend([f"Step {step_id}: {intent}"] + (sources or []))
            step_results.append({
                "step_id": step_id,
                "intent": intent,
                "context_snippet": ctx_text[:2000],
                "executor_result": None,
            })
            steps_executed += 1
            cumulative_so_far = "\n".join(cumulative_parts)
            remaining_after = remaining_steps[1:]
            if retry_count_per_intent.get(intent, 0) >= max_retries_per_intent:
                should_replan, outcome_reason = (False, "")
            else:
                should_replan, outcome_reason = evaluate_step_outcome(
                    intent,
                    ctx_text,
                    sources or [],
                    step,
                )
            evaluation_status = "ok"
            evaluation_reason = ""
            if should_replan:
                evaluation_status = "failed"
                evaluation_reason = outcome_reason
            if step_results:
                step_results[-1]["evaluation_status"] = evaluation_status
                if evaluation_reason:
                    step_results[-1]["evaluation_reason"] = evaluation_reason
            if should_replan and remaining_after and replan_count < max_replan_rounds:
                action, reason, new_plan = replan_after_step(
                    user_prompt, cumulative_so_far, step_results, step, outcome_reason, remaining_after, project_id,
                )
                replan_events.append({
                    "step_id": step_id,
                    "reason": reason or outcome_reason,
                    "action": action,
                    "new_plan_summary": [s.get("intent") for s in new_plan] if new_plan else [],
                })
                if action == "replace" and new_plan:
                    replan_count += 1
                    normalized = [(_normalize_step(s, len(step_results) + 1 + i, user_prompt)) for i, s in enumerate(new_plan)]
                    remaining_steps = normalized
                    continue
                if action == "abort":
                    remaining_steps = []
                    break
            remaining_steps = remaining_after
            continue

        executor_result = None
        can_use_llm = llm_budget_ref is None or (len(llm_budget_ref) >= 2 and llm_budget_ref[0] < llm_budget_ref[1])
        if intent == "numerical_calculation" and run_numerical_executor and PythonExecutor and not free_chat_mode and can_use_llm:
            try:
                code_prompt = f"""User hỏi: "{user_prompt}"
Context có sẵn:
{ctx_text[:6000]}

Nhiệm vụ: Tạo code Python (pandas/numpy) để trả lời. Gán kết quả cuối vào biến result.
Chỉ trả về code trong block ```python ... ```, không giải thích."""
                model = _get_default_tool_model()
                resp = AIService.call_openrouter(
                    messages=[{"role": "user", "content": code_prompt}],
                    model=model,
                    temperature=0.1,
                    max_tokens=2000,
                )
                if llm_budget_ref and len(llm_budget_ref) >= 1:
                    llm_budget_ref[0] += 1
                import re
                raw = ""
                if resp and getattr(resp, "choices", None) and len(resp.choices) > 0:
                    raw = (resp.choices[0].message.content or "").strip()
                m = re.search(r'```(?:python)?\s*(.*?)```', raw, re.DOTALL) if raw else None
                code = (m.group(1).strip() if m else raw).strip() if raw else ""
                if code:
                    val, err = PythonExecutor.execute(code, result_variable="result")
                    executor_result = str(val) if val is not None else (f"(Lỗi: {err})" if err else "null")
                    ctx_text += f"\n\n--- KẾT QUẢ TÍNH TOÁN (Python Executor) ---\n{executor_result}"
            except Exception as ex:
                executor_result = f"(Lỗi: {ex})"
                ctx_text += f"\n\n--- KẾT QUẢ TÍNH TOÁN ---\n{executor_result}"
        elif intent == "numerical_calculation" and not can_use_llm and llm_budget_ref:
            ctx_text += "\n\n--- KẾT QUẢ TÍNH TOÁN ---\n(Bỏ qua: đã đạt giới hạn gọi LLM cho lượt này.)"

        block = f"\n--- [STEP {step_id}: {intent}] ---\n{ctx_text}\n"
        cumulative_parts.append(block)
        all_sources.extend([f"Step {step_id}: {intent}"] + (sources or []))
        step_results.append({
            "step_id": step_id,
            "intent": intent,
            "context_snippet": ctx_text[:2000],
            "executor_result": executor_result,
        })
        steps_executed += 1
        cumulative_so_far = "\n".join(cumulative_parts)
        remaining_after = remaining_steps[1:]

        # Dynamic re-planning: đánh giá outcome và có thể thay plan còn lại
        if retry_count_per_intent.get(intent, 0) >= max_retries_per_intent:
            should_replan, outcome_reason = (False, "")
        else:
            should_replan, outcome_reason = evaluate_step_outcome(
                intent,
                ctx_text,
                sources or [],
                step,
            )
        evaluation_status = "ok"
        evaluation_reason = ""
        if should_replan:
            evaluation_status = "failed"
            evaluation_reason = outcome_reason
        if step_results:
            step_results[-1]["evaluation_status"] = evaluation_status
            if evaluation_reason:
                step_results[-1]["evaluation_reason"] = evaluation_reason
        if should_replan and remaining_after and replan_count < max_replan_rounds:
            action, reason, new_plan = replan_after_step(
                user_prompt,
                cumulative_so_far,
                step_results,
                step,
                outcome_reason,
                remaining_after,
                project_id,
            )
            replan_events.append({
                "step_id": step_id,
                "reason": reason or outcome_reason,
                "action": action,
                "new_plan_summary": [s.get("intent") for s in new_plan] if new_plan else [],
            })
            if action == "replace" and new_plan:
                replan_count += 1
                # Chuẩn hóa new_plan và gán step_id liên tiếp
                normalized = []
                for i, s in enumerate(new_plan):
                    normalized.append(_normalize_step(s, len(step_results) + 1 + i, user_prompt))
                remaining_steps = normalized
                continue
            if action == "abort":
                remaining_steps = []
                break
        remaining_steps = remaining_after

    cumulative_context = "\n".join(cumulative_parts)
    if max_context_tokens and AIService.estimate_tokens(cumulative_context) > token_limit:
        cumulative_context = cumulative_context[: token_limit * 4]
    return cumulative_context, all_sources, step_results, replan_events, data_operation_steps
