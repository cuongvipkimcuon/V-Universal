# ai_engine.py - Router, Context, Rule Mining (AIService + context_helpers đã tách ra ai/)
import json
import re
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple, Any

import streamlit as st

from config import Config, init_services

from ai.service import AIService, _get_default_tool_model
from ai.context_helpers import (
    get_mandatory_rules as _get_mandatory_rules,
    resolve_chapter_range as _resolve_chapter_range,
    get_entity_relations as _get_entity_relations,
    get_top_relations_by_query,
    get_top_timeline_by_query,
    filter_context_items_by_embedding,
    get_related_chapter_nums,
)
from ai.hybrid_search import HybridSearch, check_semantic_intent, search_chunks_vector
from ai.query_sql import VALID_QUERY_TARGETS, build_query_sql_context, infer_query_target
from ai.router import get_v7_reminder_message, is_multi_intent_request, is_multi_step_update_data_request, SmartAIRouter, INTENT_HANDLER_MAP
from ai.evaluate import evaluate_step_outcome, replan_after_step
from ai.content import (
    suggest_relations,
    suggest_import_category,
    generate_arc_summary_from_chapters,
    generate_chapter_metadata,
    extract_timeline_events_from_content,
    get_file_sample,
    analyze_split_strategy,
    execute_split_logic,
)
from ai.rule_mining import RuleMiningSystem
from ai.context_schema import (
    normalize_context_needs,
    infer_default_context_needs,
    normalize_context_priority,
    FULL_CONTEXT_NEEDS_V8,
)
from ai.utils import (
    cap_context_to_tokens,
    cap_chat_history_to_tokens,
    ROUTER_PLANNER_CHAT_HISTORY_MAX_TOKENS,
    get_chapter_list_for_router,
    parse_chapter_range_from_query,
    extract_prefix,
    get_prefix_key_from_entity_name,
    _get_prefix_section_order_and_labels,
    _filter_bible_by_chapter_range,
    format_bible_context_by_sections,
    get_bible_index,
    get_bible_entries,
    get_timeline_events,
)

try:
    from core.arc_service import ArcService
    from core.reverse_lookup import ReverseLookupAssembler
except ImportError:
    ArcService = None
    ReverseLookupAssembler = None


# ---------- Intent context handlers (dispatch by INTENT_HANDLER_MAP) ----------
def _intent_handle_clarification(router_result: Dict, ctx: Dict) -> None:
    """Handler: clarification — chỉ thêm block làm rõ, không gather context."""
    clarification_question = router_result.get("clarification_question", "") or "Bạn có thể nói rõ hơn câu hỏi hoặc chủ đề bạn muốn hỏi?"
    ctx["context_parts"].append(
        f"[CẦN LÀM RÕ]\nHệ thống cần thêm thông tin: {clarification_question}\nTrả lời ngắn gọn, lịch sự yêu cầu user làm rõ theo gợi ý trên (không đoán bừa)."
    )
    ctx["sources"].append("❓ Clarification")
    ctx["total_tokens"] += AIService.estimate_tokens(ctx["context_parts"][-1])


def _intent_handle_template(router_result: Dict, ctx: Dict) -> None:
    """Handler: template (suggest_v7) — không thêm block đặc biệt; LLM sẽ dùng plan."""
    pass


def _intent_handle_llm_casual(router_result: Dict, ctx: Dict) -> None:
    """Handler: llm_casual — web_search hoặc chat_casual."""
    intent = router_result.get("intent", "chat_casual")
    if intent == "web_search":
        try:
            from utils.web_search import web_search as do_web_search
            search_text = do_web_search(router_result.get("rewritten_query") or "", max_results=5)
        except Exception as ex:
            search_text = f"[WEB SEARCH] Lỗi: {ex}. Trả lời dựa trên kiến thức có sẵn."
        ctx["context_parts"].append(search_text)
        ctx["total_tokens"] += AIService.estimate_tokens(search_text)
        ctx["sources"].append("🌐 Web Search")


def _intent_handle_data_operation(router_result: Dict, ctx: Dict) -> None:
    """Handler: data_operation — unified."""
    op_type = router_result.get("data_operation_type") or ""
    op_target = router_result.get("data_operation_target") or ""
    if op_target in ("bible", "relation", "timeline", "chunking"):
        ch_range = router_result.get("chapter_range")
        ch_desc = f"chương {ch_range[0]}" if (ch_range and len(ch_range) >= 1) else "chương"
        ctx["context_parts"].append(
            f"[CẬP NHẬT DỮ LIỆU - CẦN XÁC NHẬN]\n"
            f"User yêu cầu: {op_type} {op_target} cho {ch_desc}. "
            "Thao tác này chỉ thực hiện sau khi user xác nhận. Trả lời ngắn gọn: nêu rõ thao tác và đối tượng cùng chương, nhắc user xác nhận (sẽ chạy ngầm và xem như đã chấp nhận)."
        )
        ctx["sources"].append("📦 Update data (thao tác theo chương, pending confirm)")
    else:
        update_summary = router_result.get("update_summary", "") or "Ghi nhớ / cập nhật dữ liệu theo yêu cầu user."
        ctx["context_parts"].append(
            f"[CẬP NHẬT DỮ LIỆU - CẦN XÁC NHẬN]\n{update_summary}\n\nThao tác này chỉ thực hiện sau khi user xác nhận. Trả lời tóm tắt nội dung sẽ được ghi và nhắc user xác nhận trước khi thực hiện."
        )
        ctx["sources"].append("✏️ Update data (ghi nhớ quy tắc, pending confirm)")
    ctx["total_tokens"] += AIService.estimate_tokens(ctx["context_parts"][-1])


def _intent_handle_llm_with_context(router_result: Dict, ctx: Dict) -> None:
    """Handler: llm_with_context — query_Sql, check_chapter_logic, rồi search_context/numerical gathering."""
    intent = router_result.get("intent", "chat_casual")
    project_id = ctx["project_id"]
    session_state = ctx.get("session_state") or {}
    context_parts = ctx["context_parts"]
    sources = ctx["sources"]
    total_tokens = ctx["total_tokens"]
    max_context_tokens = ctx.get("max_context_tokens")
    context_needs = ctx["context_needs"]
    context_priority = ctx["context_priority"]
    current_arc_id = session_state.get("current_arc_id")
    chapter_range_mode = ctx.get("chapter_range_mode")
    chapter_range_count = ctx.get("chapter_range_count", 5)
    chapter_range = ctx.get("chapter_range")
    target_files = ctx.get("target_files") or []
    target_bible_entities = ctx.get("target_bible_entities") or []

    # query_Sql
    if intent == "query_Sql":
        arc_id = current_arc_id
        block, source_label = build_query_sql_context(router_result, project_id, arc_id=arc_id)
        if block:
            context_parts.append(block)
            total_tokens += AIService.estimate_tokens(block)
            sources.append(source_label)
        else:
            router_result["intent"] = "search_context"
            ctx["context_needs"] = ["bible", "relation"]
            context_needs = ctx["context_needs"]
            context_priority = ctx["context_priority"] = list(context_needs)
            intent = "search_context"

    # check_chapter_logic
    if intent == "check_chapter_logic":
        try:
            from core.chapter_logic_check import run_chapter_logic_check
            ch_range = router_result.get("chapter_range")
            ch_num = int(ch_range[0]) if (ch_range and len(ch_range) >= 1) else None
            if ch_num is None:
                context_parts.append("[SOÁT LOGIC CHƯƠNG] Chưa xác định được chương. Hãy nêu rõ số chương (vd: chương 3).")
                sources.append("🔍 Logic check")
            else:
                services = init_services()
                supabase = services.get("supabase") if services else None
                if not supabase:
                    context_parts.append("[SOÁT LOGIC CHƯƠNG] Không kết nối được dịch vụ.")
                    sources.append("🔍 Logic check")
                else:
                    r = supabase.table("chapters").select("id, chapter_number, title, content, arc_id").eq(
                        "story_id", project_id
                    ).eq("chapter_number", ch_num).limit(1).execute()
                    row = (r.data or [None])[0] if r.data else None
                    if not row:
                        context_parts.append("[SOÁT LOGIC CHƯƠNG] Không tìm thấy chương %s." % ch_num)
                        sources.append("🔍 Logic check")
                    else:
                        dimensions = ctx.get("logic_check_dimensions")  # optional filter from Data Health
                        issues, resolved_count, _check_id, err = run_chapter_logic_check(
                            project_id,
                            row["id"],
                            row.get("chapter_number") or ch_num,
                            row.get("title") or ("Chương %s" % ch_num),
                            row.get("content") or "",
                            arc_id=row.get("arc_id"),
                            dimensions=dimensions,
                        )
                        if err:
                            context_parts.append("[SOÁT LOGIC CHƯƠNG] Lỗi: %s" % err)
                        else:
                            lines = ["[KẾT QUẢ SOÁT LOGIC - Chương %s]" % ch_num]
                            if not issues:
                                lines.append("Không phát hiện lỗi logic (timeline, bible, relation, chat crystallize, rule).")
                            else:
                                for i, it in enumerate(issues, 1):
                                    lines.append("%s. [%s] %s" % (i, it.get("dimension", ""), it.get("message", "")))
                            if resolved_count:
                                lines.append("(Đã đánh dấu khắc phục %s lỗi cũ từ lần soát trước.)" % resolved_count)
                            lines.append("Xem chi tiết tại **Data Health**.")
                            context_parts.append("\n".join(lines))
                        sources.append("🔍 Logic check (Data Health)")
            total_tokens = sum(AIService.estimate_tokens(p) for p in context_parts)
        except Exception as ex:
            context_parts.append("[SOÁT LOGIC CHƯƠNG] Lỗi: %s" % str(ex))
            sources.append("🔍 Logic check")
        ctx["total_tokens"] = total_tokens
        return

    # search_context / numerical_calculation: full context gathering
    intent = router_result.get("intent", "chat_casual")
    if intent not in ("search_context", "numerical_calculation"):
        ctx["total_tokens"] = total_tokens
        return

    range_bounds_bible = ContextManager._resolve_chapter_range(
        project_id, chapter_range_mode, chapter_range_count, chapter_range
    )
    # Scope: arc + sequential. Resolve arc_id từ chapter_range nếu có, else session current_arc_id
    scope_arc_id = current_arc_id
    if range_bounds_bible and ArcService:
        try:
            services = init_services()
            supabase = services.get("supabase") if services else None
            if supabase:
                r = supabase.table("chapters").select("arc_id").eq("story_id", project_id).gte(
                    "chapter_number", range_bounds_bible[0]
                ).lte("chapter_number", range_bounds_bible[1]).limit(1).execute()
                if r.data and r.data[0].get("arc_id"):
                    scope_arc_id = r.data[0]["arc_id"]
        except Exception:
            pass
    scope = ArcService.get_chapter_scope(project_id, scope_arc_id) if ArcService else {}
    chapter_ids = scope.get("chapter_ids") or []
    chapter_numbers = list(scope.get("chapter_numbers") or [])
    arc_ids = scope.get("arc_ids") or []
    # Metadata cho full-chapter fallback: mỗi part có source + chapter_numbers
    context_parts_meta = ctx.get("context_parts_meta")
    if context_parts_meta is None:
        context_parts_meta = []
        ctx["context_parts_meta"] = context_parts_meta

    # Ngân sách context khả dụng cho toàn bộ nguồn (chỉ dùng tối đa 90% budget của user, 10% để phòng sai số / câu trả lời).
    usable_tokens = None
    if max_context_tokens is not None:
        try:
            usable_tokens = max(0, int(max_context_tokens * 0.9))
        except Exception:
            usable_tokens = None

    def _over_budget() -> bool:
        if usable_tokens is None:
            return False
        return total_tokens >= usable_tokens

    # Phân bổ ngân sách giữa các nguồn context dựa trên usable_tokens.
    # Nếu không có max_context_tokens (ví dụ call nội bộ), fallback về ngưỡng mặc định cứng.
    if usable_tokens is not None and usable_tokens > 0:
        # Chunks là nguồn chính: ~60% ngân sách khả dụng
        chunk_token_budget = max(2000, int(usable_tokens * 0.6))
        # Bible: ~15%, Relations: ~10%, Timeline: ~5% (phần còn lại dành cho các nguồn khác đã được add trước đó)
        bible_token_budget = max(800, int(usable_tokens * 0.15))
        relation_token_budget = max(500, int(usable_tokens * 0.10))
        timeline_token_budget = max(400, int(usable_tokens * 0.05))

        # Ước lượng thô số token trung bình cho mỗi item để chuyển ngân sách token thành số lượng item.
        # Các giá trị này chỉ để scale tương đối theo budget, không cần chính xác tuyệt đối.
        avg_bible_tokens = 120
        avg_relation_tokens = 60
        avg_timeline_tokens = 80

        CHUNK_MAX_TOKENS = chunk_token_budget
        BIBLE_MAX_ITEMS = max(5, min(80, bible_token_budget // avg_bible_tokens))
        RELATION_MAX_ITEMS = max(20, min(200, relation_token_budget // avg_relation_tokens))
        RELATION_TOP_K = min(40, RELATION_MAX_ITEMS)
        TIMELINE_MAX_ITEMS = max(10, min(80, timeline_token_budget // avg_timeline_tokens))
        TIMELINE_VECTOR_TOP_K = min(30, TIMELINE_MAX_ITEMS)
    else:
        # Fallback khi không có budget rõ ràng
        CHUNK_MAX_TOKENS = 20000
        BIBLE_MAX_ITEMS = 15
        RELATION_TOP_K = 15
        RELATION_MAX_ITEMS = 80
        TIMELINE_MAX_ITEMS = 30
        TIMELINE_VECTOR_TOP_K = 10

    query_for_vec = (router_result.get("rewritten_query") or "").strip()
    # Planner targets chi tiết cho từng nguồn (nếu có)
    target_chunk_keywords = router_result.get("target_chunk_keywords") or []
    target_relation_entities = router_result.get("target_relation_entities") or []
    target_timeline_keywords = router_result.get("target_timeline_keywords") or []
    query_emb = ctx.get("query_embedding")
    raw_inferred = router_result.get("inferred_prefixes") or []
    valid_keys = Config.get_valid_prefix_keys()
    inferred_prefixes = [
        p for p in raw_inferred
        if p and str(p).strip().upper().replace(" ", "_") in valid_keys
    ] if valid_keys else raw_inferred

    # Thứ tự ưu tiên: chunk → bible → relation → timeline (không load full chapter ở đây; full chapter chỉ khi fallback)
    # 1) Chunk (vector, scope)
    if "chunk" in context_needs and not _over_budget():
        # Ưu tiên từ khóa chunk do planner suy ra; fallback về rewritten_query
        if target_chunk_keywords:
            # Ghép các keyword thành một câu truy vấn súc tích
            query_for_chunk = " ; ".join(str(k) for k in target_chunk_keywords if k)[:300]
        else:
            query_for_chunk = query_for_vec or "nội dung"
        # Flow ưu tiên: nếu có chapter_range + target_bible_entities (ví dụ "trận chiến của Cường từ chương 1-30"):
        # - Lấy EVENT/ACTION trong Bible liên quan nhân vật trong khoảng chapter_range.
        # - Reverse lookup ra chunk_ids candidate.
        # - Vector search chỉ trong phạm vi candidate để tăng độ chính xác cho "trận chiến" thay vì toàn bộ chunks.
        chunk_rows: List[Dict[str, Any]] = []
        candidate_chunk_ids: List[str] = []
        if range_bounds_bible and target_bible_entities:
            try:
                from ai.context_helpers import get_event_action_chunks_for_characters

                candidate_chunk_ids = get_event_action_chunks_for_characters(
                    project_id,
                    target_bible_entities,
                    chapter_range=range_bounds_bible,
                    max_chunks=120,
                )
            except Exception:
                candidate_chunk_ids = []
        if candidate_chunk_ids:
            from ai.hybrid_search import search_chunks_vector_in_candidates

            chunk_rows = search_chunks_vector_in_candidates(
                query_for_chunk,
                project_id,
                candidate_chunk_ids,
                top_k=40,
                query_embedding=query_emb,
                min_similarity=0.6,
            )
        if not chunk_rows:
            # Fallback: vector search trên toàn bộ chunks (theo arc/scope)
            chunk_rows = search_chunks_vector(
                query_for_chunk, project_id,
                arc_id=current_arc_id if not arc_ids else None,
                arc_ids=arc_ids if arc_ids else None,
                top_k=40,
                query_embedding=query_emb,
            )
            if not chunk_rows and (current_arc_id or arc_ids):
                chunk_rows = search_chunks_vector(query_for_chunk, project_id, arc_id=None, arc_ids=None, top_k=40, query_embedding=query_emb)
        chunk_rows = filter_context_items_by_embedding(chunk_rows)
        chunk_chapter_nums = set()
        if chunk_rows and ReverseLookupAssembler:
            chunk_ids_primary = [str(c.get("id")) for c in chunk_rows if c.get("id")]
            # Ưu tiên chunk trúng vector, nhưng có thể bổ sung thêm:
            # - Chunk theo chapter_range (scope user hỏi).
            # - Chunk reverse từ Bible (entity trong planner).
            extra_chunk_ids: List[str] = []
            bible_chunk_ids: List[str] = []
            if range_bounds_bible:
                try:
                    start_rb, end_rb = int(range_bounds_bible[0]), int(range_bounds_bible[1])
                    target_ch_nums = list(range(start_rb, end_rb + 1))
                except (TypeError, ValueError, IndexError):
                    target_ch_nums = []
                if target_ch_nums:
                    try:
                        extra_chunk_ids = ContextManager.get_chunks_for_chapters(
                            project_id, target_ch_nums, max_chunks=60
                        )
                    except Exception:
                        extra_chunk_ids = []
            if target_bible_entities:
                try:
                    bible_chunk_ids = ContextManager.get_chunks_for_bible_entities(
                        project_id, target_bible_entities, max_chunks=60
                    )
                except Exception:
                    bible_chunk_ids = []
            # Gộp và dedupe: chunk vector trước, rồi tới chunk theo chương, rồi chunk từ bible.
            all_chunk_ids: List[str] = []
            seen_ids = set()
            for cid in (chunk_ids_primary or []) + (extra_chunk_ids or []) + (bible_chunk_ids or []):
                if cid and cid not in seen_ids:
                    seen_ids.add(cid)
                    all_chunk_ids.append(cid)
            if all_chunk_ids:
                chunk_ctx, chunk_sources, chunk_tokens = ContextManager.build_context_with_chunk_reverse_lookup(
                    project_id, all_chunk_ids, current_arc_id, token_limit=CHUNK_MAX_TOKENS
                )
                if chunk_ctx:
                    for c in chunk_rows:
                        ch = c.get("chapter_id")
                        if ch and c.get("meta_json"):
                            try:
                                import json as _j
                                m = _j.loads(c["meta_json"]) if isinstance(c["meta_json"], str) else c["meta_json"]
                                ch_num = m.get("chapter_number") or m.get("chapter")
                                if ch_num is not None:
                                    chunk_chapter_nums.add(int(ch_num))
                            except Exception:
                                pass
                    # Nếu có thêm chunk theo chương mục tiêu, đảm bảo chapter_numbers phản ánh đúng để router/planner biết đã có semantic cho các chương đó.
                    if range_bounds_bible:
                        try:
                            start_rb, end_rb = int(range_bounds_bible[0]), int(range_bounds_bible[1])
                            for n in range(start_rb, end_rb + 1):
                                chunk_chapter_nums.add(int(n))
                        except (TypeError, ValueError, IndexError):
                            pass
                    context_parts.append(chunk_ctx)
                    total_tokens += chunk_tokens
                    sources.extend(chunk_sources)
                    sources.append("📦 Chunks")
                    context_parts_meta.append({"source": "chunk", "chapter_numbers": list(chunk_chapter_nums) or chapter_numbers[:10], "text": chunk_ctx})

    # 2) Bible (vector, scope)
    if ("bible" in context_needs or "relation" in context_needs) and not _over_budget():
        bible_context = ""
        bible_chapter_nums = set()
        for entity in target_bible_entities[:5]:
            raw_list = HybridSearch.smart_search_hybrid_raw(
                entity, project_id, top_k=7, inferred_prefixes=inferred_prefixes,
                query_embedding=query_emb,
                chapter_numbers=chapter_numbers if arc_ids else None,
            )
            raw_list = filter_context_items_by_embedding(raw_list)[:BIBLE_MAX_ITEMS]
            if range_bounds_bible:
                raw_list = _filter_bible_by_chapter_range(raw_list, range_bounds_bible, max_items=10)
            if raw_list:
                for r in raw_list:
                    if r.get("source_chapter") is not None:
                        bible_chapter_nums.add(int(r["source_chapter"]))
                for item in raw_list:
                    try:
                        eid = item.get("id")
                        if eid is not None:
                            HybridSearch.update_lookup_stats(eid)
                    except Exception:
                        pass
                main_id = raw_list[0].get("id") if raw_list else None
                rel_block = ""
                if main_id:
                    rel_text = ContextManager.get_entity_relations(main_id, project_id)
                    if rel_text:
                        rel_block = f"> [RELATION]:\n{rel_text}\n\n"
                part = format_bible_context_by_sections(raw_list)
                bible_context += f"\n--- {entity.upper()} ---\n{rel_block}{part}\n"
        if not bible_context and query_for_vec:
            raw_list = HybridSearch.smart_search_hybrid_raw(
                query_for_vec, project_id, top_k=10, inferred_prefixes=inferred_prefixes,
                query_embedding=query_emb,
                chapter_numbers=chapter_numbers if arc_ids else None,
            )
            raw_list = filter_context_items_by_embedding(raw_list)[:BIBLE_MAX_ITEMS]
            if range_bounds_bible:
                raw_list = _filter_bible_by_chapter_range(raw_list, range_bounds_bible, max_items=12)
            if raw_list:
                for r in raw_list:
                    if r.get("source_chapter") is not None:
                        bible_chapter_nums.add(int(r["source_chapter"]))
                for item in raw_list:
                    try:
                        eid = item.get("id")
                        if eid is not None:
                            HybridSearch.update_lookup_stats(eid)
                    except Exception:
                        pass
                main_id = raw_list[0].get("id") if raw_list else None
                rel_block = ""
                if main_id:
                    rel_text = ContextManager.get_entity_relations(main_id, project_id)
                    if rel_text:
                        rel_block = f"> [RELATION]:\n{rel_text}\n\n"
                part = format_bible_context_by_sections(raw_list)
                bible_context = f"\n--- KNOWLEDGE BASE ---\n{rel_block}{part}\n"
        if bible_context:
            context_parts.append(bible_context)
            total_tokens += AIService.estimate_tokens(bible_context)
            sources.append("📚 Bible Search")
            context_parts_meta.append({"source": "bible", "chapter_numbers": list(bible_chapter_nums), "text": bible_context})

        # Reverse lookup từ Bible entity -> chương liên quan -> full content (budget thấp hơn, ưu tiên theo scope)
        try:
            # Nếu đã gần hết budget context thì bỏ qua bước auto reverse lookup nặng
            if not _over_budget() and max_context_tokens and total_tokens < max_context_tokens * 0.85:
                related_chapter_nums_list = get_related_chapter_nums(project_id, target_bible_entities or [])
                if related_chapter_nums_list:
                    # Ưu tiên chương nằm trong scope hiện tại (chapter_numbers) hoặc range_bounds_bible nếu có
                    prioritized: List[int] = []
                    in_scope = set(chapter_numbers or [])
                    if range_bounds_bible:
                        start_rb, end_rb = int(range_bounds_bible[0]), int(range_bounds_bible[1])
                        in_range = {n for n in related_chapter_nums_list if start_rb <= n <= end_rb}
                    else:
                        in_range = set()
                    for n in related_chapter_nums_list:
                        if n in in_scope or n in in_range:
                            prioritized.append(n)
                    # Bổ sung phần còn lại (ngoài scope) nếu vẫn còn slot
                    for n in related_chapter_nums_list:
                        if n not in prioritized:
                            prioritized.append(n)
                    # Giới hạn số chương auto-load để tránh bùng token
                    MAX_AUTO_REVERSE_CHAPTERS = 15
                    chosen = prioritized[:MAX_AUTO_REVERSE_CHAPTERS]
                    if chosen:
                        services = init_services()
                        supabase = services["supabase"] if services else None
                        if supabase:
                            chap_res = supabase.table("chapters").select("title, chapter_number").eq(
                                "story_id", project_id
                            ).in_("chapter_number", chosen).order("chapter_number").execute()
                            auto_files = [c["title"] for c in (chap_res.data or []) if c.get("title")]
                            if auto_files:
                                # Dùng token_limit riêng cho auto reverse lookup để tránh chiếm hết context
                                auto_token_limit = max_context_tokens // 3 if max_context_tokens else 12000
                                if max_context_tokens is not None:
                                    # Không để tổng token vượt quá max_context_tokens
                                    remaining_budget = max_context_tokens - total_tokens
                                    if remaining_budget <= 0:
                                        auto_files = []
                                    else:
                                        auto_token_limit = min(auto_token_limit, max(1000, remaining_budget))
                                if auto_files:
                                    extra_text, extra_sources = ContextManager.load_full_content(
                                        auto_files, project_id, token_limit=auto_token_limit
                                    )
                                    if extra_text:
                                        # Khi đã load full chapter cho một số chương, loại bỏ bớt
                                        # các block chunk/bible/timeline/relation trùng chương đó để tránh lặp.
                                        try:
                                            chosen_set = set(chosen)
                                            to_remove_texts = set()
                                            for meta in list(context_parts_meta):
                                                src = meta.get("source")
                                                if src in ("chunk", "bible", "timeline", "relation"):
                                                    ch_nums = set(meta.get("chapter_numbers") or [])
                                                    if ch_nums & chosen_set:
                                                        txt = meta.get("text") or ""
                                                        if txt:
                                                            to_remove_texts.add(txt)
                                                        context_parts_meta.remove(meta)
                                            if to_remove_texts:
                                                context_parts[:] = [p for p in context_parts if p not in to_remove_texts]
                                        except Exception:
                                            pass
                                        context_parts.append(f"\n--- 🕵️ AUTO-DETECTED CONTEXT (REVERSE LOOKUP) ---\n{extra_text}")
                                        sources.extend([f"{s} (Auto)" for s in extra_sources])
                                        total_tokens += AIService.estimate_tokens(extra_text)
                                        context_parts_meta.append(
                                            {"source": "chunk", "chapter_numbers": chosen, "text": extra_text}
                                        )
        except Exception as e:
            print(f"Reverse lookup error: {e}")

    # 3) Relation (vector, scope)
    if "relation" in context_needs and not _over_budget():
        # Ưu tiên entity chính do planner suy ra; fallback về rewritten_query
        if target_relation_entities:
            rel_query = " ; ".join(str(e) for e in target_relation_entities if e)[:300]
        else:
            rel_query = query_for_vec or "quan hệ"
        rel_vec = get_top_relations_by_query(
            project_id, rel_query,
            top_k=RELATION_TOP_K,
            query_embedding=query_emb,
            chapter_numbers=chapter_numbers if arc_ids else None,
            max_relations=RELATION_MAX_ITEMS,
        )
        if rel_vec:
            context_parts.append(f"\n--- 🔗 {rel_vec}")
            total_tokens += AIService.estimate_tokens(rel_vec)
            sources.append("🔗 Relations (vector)")
            context_parts_meta.append({"source": "relation", "chapter_numbers": chapter_numbers[:20], "text": rel_vec})

    # 4) Timeline (vector + list, scope)
    if "timeline" in context_needs and not _over_budget():
        events = get_timeline_events(
            project_id,
            limit=TIMELINE_MAX_ITEMS,
            chapter_range=range_bounds_bible,
            arc_id=current_arc_id if not arc_ids else None,
            chapter_ids=chapter_ids if chapter_ids else None,
            arc_ids=arc_ids if arc_ids else None,
        )
        events = filter_context_items_by_embedding(events)
        if events:
            lines = ["[TIMELINE EVENTS - Thứ tự sự kiện / mốc thời gian]"]
            for e in events:
                order = e.get("event_order", 0)
                title = e.get("title", "")
                desc = (e.get("description") or "")[:400]
                raw_date = e.get("raw_date", "")
                etype = e.get("event_type", "event")
                lines.append(f"- #{order} [{etype}] {title}" + (f" (Thời điểm: {raw_date})" if raw_date else "") + f"\n  {desc}")
            block = "\n".join(lines)
            context_parts.append(block)
            total_tokens += AIService.estimate_tokens(block)
            sources.append("📅 Timeline Events")
            context_parts_meta.append({"source": "timeline", "chapter_numbers": chapter_numbers[:20], "text": block})
        else:
            # Ưu tiên từ khóa timeline do planner suy ra; fallback về rewritten_query
            if target_timeline_keywords:
                tl_query = " ; ".join(str(k) for k in target_timeline_keywords if k)[:300]
            else:
                tl_query = query_for_vec or "sự kiện"
            tl_vec = get_top_timeline_by_query(
                project_id, tl_query,
                top_k=TIMELINE_VECTOR_TOP_K,
                query_embedding=query_emb,
                chapter_ids=chapter_ids[:500] if chapter_ids else None,
                arc_ids=arc_ids if arc_ids else None,
                max_events=80,
            )
            if tl_vec:
                context_parts.append(f"\n--- 📅 {tl_vec}")
                total_tokens += AIService.estimate_tokens(tl_vec)
                sources.append("📅 Timeline (vector)")
                context_parts_meta.append({"source": "timeline", "chapter_numbers": chapter_numbers[:20], "text": tl_vec})
            else:
                context_parts.append("[TIMELINE] Chưa có dữ liệu timeline_events. Trả lời dựa trên Bible/chương nếu có.")
                sources.append("📅 Timeline (empty)")

    # Kiến trúc giống router: primary = retrieval (chunk, bible, timeline, relation);
    # fallback = load full chương CHỈ KHI không tìm được bất kỳ chunk/bible/relation/timeline nào
    # thuộc các chương mà user đã đề cập (chapter_range).
    if intent == "search_context" and range_bounds_bible and not _over_budget():
        has_semantic_for_target_chapters = False
        target_ch_set = set()
        try:
            start_rb, end_rb = int(range_bounds_bible[0]), int(range_bounds_bible[1])
            if start_rb <= end_rb:
                target_ch_set = {int(n) for n in range(start_rb, end_rb + 1)}
        except (TypeError, ValueError, IndexError):
            target_ch_set = set()

        if target_ch_set:
            for meta in context_parts_meta or []:
                src = (meta.get("source") or "").strip().lower()
                if src in ("chunk", "bible", "timeline", "relation"):
                    text_meta = (meta.get("text") or "").strip()
                    ch_nums = meta.get("chapter_numbers") or []
                    if not text_meta or not ch_nums:
                        continue
                    try:
                        ch_in_meta = {int(x) for x in ch_nums if x is not None}
                    except Exception:
                        continue
                    if ch_in_meta & target_ch_set:
                        has_semantic_for_target_chapters = True
                        break
        else:
            # Nếu không resolve được chapter_range rõ ràng, fallback về kiểm tra có semantic tổng quát hay không.
            for meta in context_parts_meta or []:
                src = (meta.get("source") or "").strip().lower()
                if src in ("chunk", "bible", "timeline", "relation"):
                    text_meta = (meta.get("text") or "").strip()
                    if text_meta:
                        has_semantic_for_target_chapters = True
                        break

        # Chỉ khi KHÔNG có bất kỳ semantic nào (chunk/bible/relation/timeline) thuộc chapter_range thì mới load full chương.
        if not has_semantic_for_target_chapters:
            try:
                start_rb, end_rb = int(range_bounds_bible[0]), int(range_bounds_bible[1])
            except (TypeError, ValueError, IndexError):
                start_rb = end_rb = None
            if start_rb is not None and end_rb is not None and start_rb <= end_rb:
                # Dùng budget còn lại để load full chương, không vượt DEFAULT_CHAPTER_TOKEN_LIMIT; giữ nguyên bible/chunk đã có.
                token_limit_for_fallback = ContextManager.DEFAULT_CHAPTER_TOKEN_LIMIT
                if max_context_tokens is not None:
                    remaining_budget = max_context_tokens - total_tokens
                    if remaining_budget <= 0:
                        token_limit_for_fallback = 0
                    else:
                        token_limit_for_fallback = min(
                            ContextManager.DEFAULT_CHAPTER_TOKEN_LIMIT,
                            max(1000, remaining_budget),
                        )
                if token_limit_for_fallback > 0:
                    fallback_text, fallback_sources = ContextManager.load_chapters_by_range(
                        project_id,
                        start_rb,
                        end_rb,
                        token_limit=token_limit_for_fallback,
                    )
                    if fallback_text:
                        context_parts.append(
                            "\n--- NỘI DUNG CHƯƠNG (FALLBACK - không tìm thấy chunk/bible/relation/timeline thuộc chapter_range; vẫn giữ Bible/chunk nếu còn token) ---\n"
                            + fallback_text
                        )
                        total_tokens += AIService.estimate_tokens(fallback_text)
                        if fallback_sources:
                            sources.extend(fallback_sources)
                        context_parts_meta.append(
                            {
                                "source": "chapter_full",
                                "chapter_numbers": list(range(start_rb, end_rb + 1)),
                                "text": fallback_text,
                            }
                        )

    ctx["total_tokens"] = total_tokens


INTENT_CONTEXT_HANDLERS = {
    "clarification": _intent_handle_clarification,
    "template": _intent_handle_template,
    "llm_casual": _intent_handle_llm_casual,
    "data_operation": _intent_handle_data_operation,
    "llm_with_context": _intent_handle_llm_with_context,
}


# ==========================================
# 📚 CONTEXT MANAGER (V5 + V6 Arc & Reverse Lookup)
# ==========================================
class ContextManager:
    """Quản lý context cho AI với khả năng kết hợp nhiều nguồn. V6: Arc scoping + Triangle assembler."""

    @staticmethod
    def _build_arc_scope_context(project_id: str, current_arc_id: Optional[str], session_state: Optional[Dict] = None) -> Tuple[str, int]:
        """
        V6 MODULE 1 & 3: Build [Past Arc Summaries] + [Current Arc] for Sequential/Standalone.
        Global Bible is still injected via get_mandatory_rules and search_bible below.
        Returns (text, estimated_tokens).
        """
        if not ArcService or not current_arc_id:
            return "", 0
        arc = ArcService.get_arc(current_arc_id)
        if not arc:
            return "", 0
        parts = []
        scope = ArcService.get_scope_for_search(project_id, current_arc_id)
        if scope.get("scope_type") == ArcService.ARC_TYPE_SEQUENTIAL and scope.get("arc_summaries"):
            parts.append("[PAST ARC SUMMARIES - Timeline Inheritance]")
            for a in scope["arc_summaries"]:
                parts.append("- ARC: %s\n  Summary: %s" % (a.get("name", ""), (a.get("summary") or "").strip() or "(none)"))
            parts.append("")
        parts.append("[MACRO CONTEXT - ARC: %s]" % (arc.get("name") or "Current"))
        parts.append("Summary: %s" % ((arc.get("summary") or "").strip() or "(none)"))
        text = "\n".join(parts)
        return text, AIService.estimate_tokens(text)

    @staticmethod
    def get_chunks_for_chapters(
        project_id: str,
        chapter_numbers: List[int],
        max_chunks: int = 60,
    ) -> List[str]:
        """
        Lấy danh sách id chunk thuộc các chương được chỉ định (chapter_numbers) của project.
        Dùng để ưu tiên chunk theo chapter_range thay vì load full chapter.
        """
        if not project_id or not chapter_numbers:
            return []
        try:
            services = init_services()
            if not services:
                return []
            supabase = services["supabase"]
        except Exception:
            return []

        try:
            # Resolve chapter_ids từ chapter_numbers
            ch_set = {int(n) for n in chapter_numbers if n is not None}
            if not ch_set:
                return []
            r = (
                supabase.table("chapters")
                .select("id, chapter_number")
                .eq("story_id", project_id)
                .in_("chapter_number", list(ch_set))
                .execute()
            )
            rows = list(r.data or [])
            chapter_ids = [row["id"] for row in rows if row.get("id")]
            if not chapter_ids:
                return []
            cr = (
                supabase.table("chunks")
                .select("id, chapter_id, sort_order")
                .eq("story_id", project_id)
                .in_("chapter_id", chapter_ids)
                .order("chapter_id")
                .order("sort_order")
                .limit(max_chunks)
                .execute()
            )
            chunks = list(cr.data or [])
            out_ids: List[str] = []
            for row in chunks:
                cid = row.get("id")
                if cid:
                    out_ids.append(str(cid))
            return out_ids
        except Exception:
            return []

    @staticmethod
    def get_chunks_for_bible_entities(
        project_id: str,
        entity_names: List[str],
        max_chunks: int = 60,
    ) -> List[str]:
        """
        Reverse lookup từ Bible -> Chunk:
        - Ưu tiên source_chunk_id trên story_bible (nếu có).
        - Bổ sung chunk_id từ chunk_bible_links.
        - Nếu vẫn còn slot, dùng source_chapter của entity để lấy thêm chunk theo chương.
        """
        if not project_id or not entity_names:
            return []
        try:
            services = init_services()
            if not services:
                return []
            supabase = services["supabase"]
        except Exception:
            return []

        norm_names = [str(n).strip() for n in entity_names if n and str(n).strip()]
        if not norm_names:
            return []

        bible_ids: List[Any] = []
        chunk_ids: List[str] = []
        chapter_nums: List[int] = []
        try:
            # Tìm entity theo tên (ilike); giới hạn mỗi tên để tránh quá nặng.
            for name in norm_names:
                try:
                    r = (
                        supabase.table("story_bible")
                        .select("id, source_chapter, source_chunk_id")
                        .eq("story_id", project_id)
                        .ilike("entity_name", f"%{name}%")
                        .limit(30)
                        .execute()
                    )
                except Exception:
                    continue
                for row in (r.data or []):
                    bid = row.get("id")
                    if bid and bid not in bible_ids:
                        bible_ids.append(bid)
                    scid = row.get("source_chunk_id")
                    if scid and scid not in chunk_ids:
                        chunk_ids.append(str(scid))
                    ch_num = row.get("source_chapter")
                    if ch_num is not None:
                        try:
                            val = int(ch_num)
                            if val > 0:
                                chapter_nums.append(val)
                        except Exception:
                            continue

            # Từ chunk_bible_links: chunk_id liên kết với các bible entity đã tìm được.
            if bible_ids and len(chunk_ids) < max_chunks:
                try:
                    cb = (
                        supabase.table("chunk_bible_links")
                        .select("chunk_id, bible_entry_id")
                        .eq("story_id", project_id)
                        .in_("bible_entry_id", bible_ids)
                        .limit(max_chunks * 2)
                        .execute()
                    )
                    for row in (cb.data or []):
                        cid = row.get("chunk_id")
                        if cid and str(cid) not in chunk_ids:
                            chunk_ids.append(str(cid))
                            if len(chunk_ids) >= max_chunks:
                                break
                except Exception:
                    pass

            # Nếu vẫn còn slot, dùng chương chứa entity để lấy thêm chunk theo chương.
            if len(chunk_ids) < max_chunks and chapter_nums:
                try:
                    extra = ContextManager.get_chunks_for_chapters(
                        project_id,
                        chapter_numbers=chapter_nums,
                        max_chunks=max_chunks * 2,
                    )
                    for cid in extra:
                        if cid not in chunk_ids:
                            chunk_ids.append(cid)
                            if len(chunk_ids) >= max_chunks:
                                break
                except Exception:
                    pass

            return chunk_ids[:max_chunks]
        except Exception:
            return []

    @staticmethod
    def build_context_with_chunk_reverse_lookup(
        project_id: str,
        chunk_ids: List[str],
        current_arc_id: Optional[str],
        token_limit: int = 12000,
    ) -> Tuple[str, List[str], int]:
        """
        V6 MODULE 3: Assemble context from chunk IDs using Triangle (Macro/Meso/Micro).
        Optionally prepend arc scope. Returns (full_context, sources, total_tokens).
        """
        context_parts = []
        sources = []
        total_tokens = 0
        if ArcService and current_arc_id:
            arc_scope, t = ContextManager._build_arc_scope_context(project_id, current_arc_id, None)
            if arc_scope:
                context_parts.append(arc_scope)
                total_tokens += t
        if ReverseLookupAssembler and chunk_ids:
            assembled, chunk_sources = ReverseLookupAssembler.assemble_from_chunks(chunk_ids, token_limit=token_limit)
            if assembled:
                context_parts.append("[REVERSE LOOKUP - Micro to Macro Evidence]\n" + assembled)
                total_tokens += AIService.estimate_tokens(assembled)
                sources.extend(chunk_sources)
        return "\n\n".join(context_parts), sources, total_tokens

    @staticmethod
    def get_entity_relations(entity_id: Any, project_id: str) -> str:
        """Ủy quyền cho ai.context_helpers.get_entity_relations."""
        return _get_entity_relations(entity_id, project_id)

    # Giới hạn token khi load nhiều chương (ưu tiên summary nếu vượt)
    DEFAULT_CHAPTER_TOKEN_LIMIT = 60000

    @staticmethod
    def _resolve_chapter_range(
        project_id: str,
        chapter_range_mode: Optional[str],
        chapter_range_count: int,
        chapter_range: Optional[List[int]],
    ) -> Optional[Tuple[int, int]]:
        """Ủy quyền cho ai.context_helpers.resolve_chapter_range."""
        return _resolve_chapter_range(project_id, chapter_range_mode, chapter_range_count, chapter_range)

    @staticmethod
    def load_chapters_by_range(
        project_id: str,
        start: int,
        end: int,
        token_limit: int = 60000,
    ) -> Tuple[str, List[str]]:
        """Load chương theo khoảng chapter_number; có summary và art_style; nếu vượt token_limit thì ưu tiên summary cho chương cũ, full content cho chương đang bàn (cuối)."""
        try:
            services = init_services()
            if not services:
                return "", []
            supabase = services["supabase"]
            r = supabase.table("chapters").select("*").eq(
                "story_id", project_id
            ).gte("chapter_number", start).lte("chapter_number", end).order(
                "chapter_number"
            ).execute()
            rows = r.data if r.data else []
        except Exception as e:
            print(f"load_chapters_by_range error: {e}")
            return "", []

        full_text = ""
        loaded_sources = []
        total_tokens = 0
        focus_idx = len(rows) - 1 if rows else -1

        for i, item in enumerate(rows):
            title = item.get("title") or f"Chương {item.get('chapter_number', i+1)}"
            content = item.get("content") or ""
            summary = item.get("summary") or ""
            art_style = item.get("art_style") or ""
            use_full = (token_limit <= 0 or total_tokens < token_limit) or (i == focus_idx)
            block = f"\n\n=== 📄 {title} ===\n"
            if summary:
                block += f"[Summary]: {summary}\n"
            if art_style:
                block += f"[Art style]: {art_style}\n"
            if use_full and content:
                block += f"[Content]:\n{content}\n"
            elif summary and not use_full:
                block += f"(Chỉ tóm tắt do giới hạn token.)\n"
            full_text += block
            loaded_sources.append(f"📄 {title}")
            total_tokens += AIService.estimate_tokens(block)

        return full_text, loaded_sources

    @staticmethod
    def load_full_content(
        file_names: List[str],
        project_id: str,
        token_limit: int = 60000,
        focus_chapter_name: Optional[str] = None,
    ) -> Tuple[str, List[str]]:
        """Load nội dung file/chương; thêm summary và art_style; nếu vượt token_limit thì ưu tiên summary, full content cho chương focus."""
        if not file_names:
            return "", []

        try:
            services = init_services()
            supabase = services["supabase"]
        except Exception:
            return "", []

        full_text = ""
        loaded_sources = []
        total_tokens = 0
        rows_with_meta = []

        for name in file_names:
            try:
                res = supabase.table("chapters").select("*").eq(
                    "story_id", project_id
                ).ilike("title", f"%{name}%").execute()
            except Exception:
                res = type("Res", (), {"data": None})()

            if res.data and len(res.data) > 0:
                item = res.data[0]
                item["_name"] = name
                item["_is_focus"] = (focus_chapter_name and focus_chapter_name in (item.get("title") or ""))
                rows_with_meta.append(item)
            else:
                try:
                    res_bible = supabase.table("story_bible").select(
                        "entity_name, description"
                    ).eq("story_id", project_id).ilike("entity_name", f"%{name}%").execute()
                    if res_bible.data and len(res_bible.data) > 0:
                        item = res_bible.data[0]
                        full_text += f"\n\n=== ⚠️ BIBLE SUMMARY: {item.get('entity_name', name)} ===\n{item.get('description', '')}\n"
                        loaded_sources.append(f"🗂️ {item.get('entity_name', name)} (Summary)")
                except Exception:
                    pass

        for item in rows_with_meta:
            title = item.get("title") or f"Chương {item.get('chapter_number')}"
            content = item.get("content") or ""
            summary = item.get("summary") or ""
            art_style = item.get("art_style") or ""
            is_focus = item.get("_is_focus", False)
            use_full = token_limit <= 0 or total_tokens + AIService.estimate_tokens(content) <= token_limit or is_focus
            block = f"\n\n=== 📄 SOURCE FILE/CHAP: {title} ===\n"
            if summary:
                block += f"[Summary]: {summary}\n"
            if art_style:
                block += f"[Art style]: {art_style}\n"
            if use_full and content:
                block += f"[Content]:\n{content}\n"
            elif summary:
                block += "(Chỉ tóm tắt do giới hạn token.)\n"
            full_text += block
            loaded_sources.append(f"📄 {title}")
            total_tokens += AIService.estimate_tokens(block)

        return full_text, loaded_sources

    @staticmethod
    def get_mandatory_rules(project_id: str) -> str:
        """Ủy quyền cho ai.context_helpers.get_mandatory_rules."""
        return _get_mandatory_rules(project_id)

    @staticmethod
    def get_rules_block_by_type(project_id: str, arc_id: Optional[str], types: List[str]) -> str:
        """Wrapper: lấy block rule theo type (Style/Method/Info/Unknown)."""
        try:
            from ai.context_helpers import get_rules_by_type_block

            return get_rules_by_type_block(project_id, arc_id, types)
        except Exception:
            return ""

    @staticmethod
    def get_relevant_info_rules(
        project_id: str,
        user_prompt: str,
        arc_id: Optional[str] = None,
        threshold: float = 0.6,
        candidate_block: Optional[str] = None,
        query_embedding: Optional[List[float]] = None,
    ) -> str:
        """Wrapper: lấy Info Rules (type='Info') có similarity >= threshold với câu hỏi hiện tại, ưu tiên trong candidate_block nếu có."""
        try:
            from ai.context_helpers import get_relevant_info_rules as _get_relevant_info_rules

            return _get_relevant_info_rules(
                project_id,
                user_prompt,
                arc_id=arc_id,
                threshold=threshold,
                candidate_rules_block=candidate_block,
                query_embedding=query_embedding,
            )
        except Exception:
            return ""

    @staticmethod
    def get_crystallize_context(project_id: str, arc_id: Optional[str] = None, limit: int = 30) -> str:
        """Lấy block text từ chat_crystallize_entries (scope project + arc nếu có), theo scope khoảng chương/arc."""
        if not project_id:
            return ""
        try:
            from config import init_services
            services = init_services()
            if not services or not services.get("supabase"):
                return ""
            supabase = services["supabase"]
            lines = []
            seen_ids = set()
            q_project = supabase.table("chat_crystallize_entries").select("id, title, description").eq("scope", "project").eq("story_id", project_id)
            for row in (q_project.order("created_at", desc=True).limit(limit).execute().data or []):
                rid = row.get("id")
                if rid and rid not in seen_ids:
                    seen_ids.add(rid)
                    title = (row.get("title") or "").strip()
                    desc = (row.get("description") or "").strip()[:800]
                    if title or desc:
                        lines.append(f"  • {title}: {desc}")
            if arc_id:
                q_arc = supabase.table("chat_crystallize_entries").select("id, title, description").eq("scope", "arc").eq("story_id", project_id).eq("arc_id", arc_id)
                for row in (q_arc.order("created_at", desc=True).limit(limit).execute().data or []):
                    rid = row.get("id")
                    if rid and rid not in seen_ids:
                        seen_ids.add(rid)
                        title = (row.get("title") or "").strip()
                        desc = (row.get("description") or "").strip()[:800]
                        if title or desc:
                            lines.append(f"  • {title}: {desc}")
            if not lines:
                return ""
            return "[CHAT CRYSTALLIZE - Điểm nhớ từ hội thoại]\n" + "\n".join(lines)
        except Exception:
            return ""

    @staticmethod
    def build_context(
        router_result: Dict,
        project_id: str,
        persona: Dict,
        strict_mode: bool = False,
        current_arc_id: Optional[str] = None,
        session_state: Optional[Dict] = None,
        free_chat_mode: bool = False,
        max_context_tokens: Optional[int] = None,
        query_embedding: Optional[List[float]] = None,
        for_v7_segment: bool = False,
    ) -> Tuple[str, List[str], int]:
        """Xây dựng context từ router result. query_embedding: embedding câu hỏi (rewritten hoặc gốc) đã tính sẵn để tránh gọi API nhiều lần.
        for_v7_segment: True khi build context cho từng segment V7 — bỏ persona/style; thêm Method, Info, crystallize (scope chương/arc)."""
        context_parts = []
        sources = []
        total_tokens = 0

        if not for_v7_segment:
            persona_text = f"🎭 PERSONA: {persona['role']}\n{persona['core_instruction']}\n"
            context_parts.append(persona_text)
            total_tokens += AIService.estimate_tokens(persona_text)

        if free_chat_mode:
            rules_text = ContextManager.get_mandatory_rules(project_id)
            if rules_text:
                context_parts.append(rules_text)
                total_tokens += AIService.estimate_tokens(rules_text)
            free_instruction = "[CHẾ ĐỘ CHAT TỰ DO / CHAT PHIẾM]\nTrả lời như chatbot thông thường, dựa trên kiến thức tổng quát. Không bắt buộc dựa vào dữ liệu dự án (Bible/chunk/file); có thể trả lời mọi chủ đề."
            context_parts.append(free_instruction)
            total_tokens += AIService.estimate_tokens(free_instruction)
            sources.append("🌐 Chat tự do")
            return "\n".join(context_parts), sources, total_tokens

        # V6 MODULE 1: Arc scope (Past Arc Summaries + Current Arc)
        if current_arc_id and ArcService:
            arc_scope, arc_tokens = ContextManager._build_arc_scope_context(project_id, current_arc_id, session_state)
            if arc_scope:
                context_parts.append(arc_scope)
                total_tokens += arc_tokens
                sources.append("📐 Arc Scope")

        if strict_mode:
            strict_text = """
\n\n‼️ CHẾ ĐỘ NGHIÊM NGẶT (STRICT MODE) ĐANG BẬT:
1. CHỈ trả lời dựa trên thông tin có trong [CONTEXT] (bao gồm chunk, bible, timeline, relation). KHÔNG được giả định thêm ngoài những gì đã nêu.
2. TUYỆT ĐỐI KHÔNG bịa đặt hoặc dùng kiến thức bên ngoài dự án để điền vào chỗ trống.
3. Nếu không tìm thấy thông tin đủ rõ trong Context, hãy trả lời: "Dữ liệu dự án chưa có thông tin này." hoặc "Trong các đoạn/chunk hiện có, chưa thấy nói rõ về điểm này."
4. Chấp nhận rằng Context có thể chỉ là MỘT PHẦN chương (một số chunk). Vẫn phải trả lời dựa trên các phần hiện có, KHÔNG yêu cầu phải có toàn bộ chương đầy đủ.
5. Nếu User hỏi về "lịch sử", "cốt truyện", hãy ưu tiên trích xuất từ [KNOWLEDGE BASE] và các chunk liên quan trong Context.
6. Không từ chối trả lời các dữ liệu thực tế (fact) chỉ vì tính cách Persona.
"""
            context_parts.append(strict_text)
            total_tokens += AIService.estimate_tokens(strict_text)

        # Bước 3: Rules theo type
        # - Style: luôn bơm vào context cuối (ảnh hưởng phong cách/thoại). V7 segment: bỏ qua (chỉ nạp vào LLM trả lời cuối).
        # - Unknown: bơm nguyên văn (chưa phân loại).
        # - Info: lọc bằng vector (similarity >= 0.6 với câu hỏi) rồi mới bơm.
        # - Method: chỉ dùng ở bước 1 intent khi không V7 segment; V7 segment: tiêm vào từng segment.
        if not for_v7_segment:
            style_block = ContextManager.get_rules_block_by_type(project_id, current_arc_id, ["Style"])
            if style_block:
                rules_text = "\n🔥 --- STYLE RULES ---\n" + style_block + "\n"
                context_parts.append(rules_text)
                total_tokens += AIService.estimate_tokens(rules_text)
        if for_v7_segment:
            method_block = ContextManager.get_rules_block_by_type(project_id, current_arc_id, ["Method"])
            if method_block:
                rules_text = "\n🔥 --- METHOD RULES ---\n" + method_block + "\n"
                context_parts.append(rules_text)
                total_tokens += AIService.estimate_tokens(rules_text)
        unknown_block = ContextManager.get_rules_block_by_type(project_id, current_arc_id, ["Unknown"])
        if unknown_block:
            rules_text = "\n🔥 --- PROJECT RULES ---\n" + unknown_block + "\n"
            context_parts.append(rules_text)
            total_tokens += AIService.estimate_tokens(rules_text)
        info_block = ContextManager.get_relevant_info_rules(
            project_id,
            router_result.get("rewritten_query") or router_result.get("reason") or "",
            arc_id=current_arc_id,
            threshold=0.6,
            candidate_block=(router_result.get("included_rules_text") or router_result.get("relevant_rules") or ""),
            query_embedding=query_embedding,
        )
        if info_block:
            rules_text = "\n🔥 --- INFO RULES (gần với câu hỏi) ---\n" + info_block + "\n"
            context_parts.append(rules_text)
            total_tokens += AIService.estimate_tokens(rules_text)
        if for_v7_segment:
            crystallize_block = ContextManager.get_crystallize_context(project_id, current_arc_id, limit=30)
            if crystallize_block:
                context_parts.append("\n" + crystallize_block + "\n")
                total_tokens += AIService.estimate_tokens(crystallize_block)
                sources.append("💎 Chat Crystallize")

        intent = router_result.get("intent", "chat_casual")
        target_files = router_result.get("target_files", [])
        target_bible_entities = router_result.get("target_bible_entities", [])
        chapter_range_mode = router_result.get("chapter_range_mode")
        chapter_range_count = router_result.get("chapter_range_count", 5)
        chapter_range = router_result.get("chapter_range")
        context_needs = normalize_context_needs(router_result.get("context_needs"))
        # Chuẩn hóa intent cũ -> search_context
        if intent in ("read_full_content", "search_bible", "mixed_context", "manage_timeline", "search_chunks"):
            if not context_needs:
                if intent == "read_full_content":
                    context_needs = ["chapter"]
                elif intent == "manage_timeline":
                    context_needs = ["timeline"]
                elif intent == "search_chunks":
                    context_needs = ["chunk"]
                elif intent == "search_bible":
                    context_needs = ["bible", "relation"]
                else:
                    context_needs = ["bible", "relation", "chapter", "timeline", "chunk"]
            intent = "search_context"
        if not context_needs and intent == "search_context":
            context_needs = infer_default_context_needs(router_result)
        # V8: intent search_context — nếu bật v8_full_context_search (mặc định bật) thì gather đủ tất cả nguồn
        _v8_full = True
        if intent == "search_context":
            try:
                services = init_services()
                if services and services.get("supabase"):
                    r = services["supabase"].table("settings").select("value").eq("key", "v8_full_context_search").execute()
                    if r.data and r.data[0] and str(r.data[0].get("value") or "").strip() == "0":
                        _v8_full = False
            except Exception:
                pass
            if _v8_full:
                context_needs = list(FULL_CONTEXT_NEEDS_V8)
                context_priority = list(FULL_CONTEXT_NEEDS_V8)
            else:
                context_priority = normalize_context_priority(
                    router_result.get("context_priority"), context_needs
                ) or list(context_needs)
        else:
            context_priority = normalize_context_priority(
                router_result.get("context_priority"), context_needs
            ) or list(context_needs)

        # Dispatch theo INTENT_HANDLER_MAP (clarification, template, llm_casual, data_operation, llm_with_context)
        handler_type = INTENT_HANDLER_MAP.get(intent, "llm_with_context")
        handler_fn = INTENT_CONTEXT_HANDLERS.get(handler_type, _intent_handle_llm_with_context)
        ctx = {
            "context_parts": context_parts,
            "sources": sources,
            "total_tokens": total_tokens,
            "project_id": project_id,
            "session_state": session_state,
            "max_context_tokens": max_context_tokens,
            "context_needs": context_needs,
            "context_priority": context_priority,
            "router_result": router_result,
            "chapter_range_mode": chapter_range_mode,
            "chapter_range_count": chapter_range_count,
            "chapter_range": chapter_range,
            "target_files": target_files,
            "target_bible_entities": target_bible_entities,
            "logic_check_dimensions": None,  # Data Health có thể truyền khi gọi từ tab soát
            "query_embedding": query_embedding,
            "context_parts_meta": [],  # search_context điền để fallback full chapter strip chunk/bible/timeline/relation
        }
        handler_fn(router_result, ctx)
        context_parts = ctx["context_parts"]
        sources = ctx["sources"]
        total_tokens = ctx["total_tokens"]
        context_parts_meta = ctx.get("context_parts_meta") or []

        context_str = "\n".join(context_parts)
        if max_context_tokens is not None and total_tokens > max_context_tokens:
            context_str, total_tokens = cap_context_to_tokens(context_str, max_context_tokens)
        return context_str, sources, total_tokens, context_parts_meta


