import threading
from datetime import datetime

import streamlit as st

from config import Config, init_services, CostManager
from ai_engine import (
    AIService,
    ContextManager,
    SmartAIRouter,
    RuleMiningSystem,
    HybridSearch,
    check_semantic_intent,
    get_v7_reminder_message,
)
from ai.evaluate import is_answer_sufficient
from ai.context_helpers import get_related_chapter_nums
from ai_verifier import run_verification_loop
from core.executor_v7 import execute_plan
from core.command_parser import is_command_message, parse_command, get_fallback_clarification
from core.observability import log_chat_turn
from ai.router import is_multi_intent_request
from persona import PersonaSystem
from utils.auth_manager import check_permission, submit_pending_change
from utils.python_executor import PythonExecutor


def _get_logic_reminder(project_id):
    """V7.7: Nếu có lỗi logic đang active thì trả về đoạn nhắc; không thì ''."""
    if not project_id:
        return ""
    try:
        from core.chapter_logic_check import get_active_logic_issues_summary
        summary = get_active_logic_issues_summary(project_id)
        if not summary:
            return ""
        total = sum(s.get("count", 0) for s in summary)
        if total == 0:
            return ""
        return "\n\n---\n💡 **Nhắc:** Bạn đang có **%s** lỗi logic chưa sửa (ở %s chương). Vào **Data Health** (Knowledge) để xem và sửa." % (total, len(summary))
    except Exception:
        return ""


def _get_crystallize_count(project_id, user_id):
    """Lấy số tin nhắn từ lần crystallize gần nhất (schema v7.1). Trả về 0 nếu chưa có bảng."""
    try:
        services = init_services()
        if not services:
            return 0
        r = services["supabase"].table("chat_crystallize_state").select("messages_since_crystallize").eq(
            "story_id", project_id
        ).eq("user_id", str(user_id) or "").limit(1).execute()
        if r.data and len(r.data) > 0:
            return int(r.data[0].get("messages_since_crystallize", 0) or 0)
    except Exception:
        pass
    return 0


def _increment_crystallize_count(project_id, user_id):
    """Tăng messages_since_crystallize lên 1 (sau khi lưu tin nhắn V Work)."""
    try:
        services = init_services()
        if not services:
            return
        sb = services["supabase"]
        now = datetime.utcnow().isoformat()
        r = sb.table("chat_crystallize_state").select("messages_since_crystallize").eq(
            "story_id", project_id
        ).eq("user_id", str(user_id) or "").limit(1).execute()
        if r.data and len(r.data) > 0:
            cur = int(r.data[0].get("messages_since_crystallize", 0) or 0)
            sb.table("chat_crystallize_state").update({
                "messages_since_crystallize": cur + 1,
                "updated_at": now,
            }).eq("story_id", project_id).eq("user_id", str(user_id) or "").execute()
        else:
            sb.table("chat_crystallize_state").upsert({
                "story_id": project_id,
                "user_id": str(user_id) or "",
                "messages_since_crystallize": 1,
                "updated_at": now,
            }, on_conflict="story_id,user_id").execute()
    except Exception:
        pass


def _reset_crystallize_count(project_id, user_id):
    """Reset về 0 sau khi crystallize (tránh trùng)."""
    try:
        services = init_services()
        if not services:
            return
        now = datetime.utcnow().isoformat()
        services["supabase"].table("chat_crystallize_state").upsert({
            "story_id": project_id,
            "user_id": str(user_id) or "",
            "messages_since_crystallize": 0,
            "updated_at": now,
        }, on_conflict="story_id,user_id").execute()
    except Exception:
        pass


def _after_save_history_v_work(project_id, user_id, persona_role, allow_data_changing=False):
    """Sau khi lưu tin nhắn V Work: tăng counter, nếu >= 30 và allow_data_changing thì chạy crystallize (sẽ reset về 0)."""
    if not project_id or not user_id:
        return
    _increment_crystallize_count(project_id, user_id)
    if _get_crystallize_count(project_id, user_id) >= 30 and allow_data_changing:
        threading.Thread(
            target=_auto_crystallize_background,
            args=(project_id, user_id, persona_role),
            daemon=True,
        ).start()
    elif _get_crystallize_count(project_id, user_id) >= 30 and not allow_data_changing:
        st.session_state["crystallize_blocked_no_allow"] = True


def _start_data_operation_background(
    project_id,
    user_id,
    user_request,
    active_persona,
    now_timestamp,
    steps=None,
    single_op=None,
    unified_range=None,
    insert_user_message=True,
    rerun_after=True,
):
    """
    Chạy thao tác dữ liệu ngầm (không xác nhận): lưu user + tin 'Đang chạy ngầm', start thread.
    unified_range: (chapter_start, chapter_end) hoặc [start, end] → job unified_chapter_range (V7 Planner).
    toast, (optionally) rerun. Khi xong job sẽ tự ghi tin hoàn thành vào chat (data_operation_jobs).
    insert_user_message=False: chỉ insert tin 'Đang chạy ngầm'. rerun_after=False: không rerun (e.g. sau execute_plan để vẫn hiển thị response V7).
    Cần bật toggle "Cho phép thao tác ảnh hưởng dữ liệu" thì mới thực hiện.
    """
    if not st.session_state.get("allow_data_changing_actions", False):
        st.warning("Bật toggle **Cho phép thao tác ảnh hưởng dữ liệu** (sidebar V Work) để thực hiện thao tác extract/update/delete.")
        return
    steps = steps if isinstance(steps, list) else []
    if steps:
        desc = f"{len(steps)} thao tác (extract/update/delete)."
    elif single_op:
        op = single_op.get("operation_type", "extract")
        t = single_op.get("target", "bible")
        ch = single_op.get("chapter_number", "")
        desc = f"{op} {t} chương {ch}."
    elif unified_range and isinstance(unified_range, (list, tuple)) and len(unified_range) >= 2:
        s, e = int(unified_range[0]), int(unified_range[1])
        s, e = min(s, e), max(s, e)
        desc = f"Unified analyze chương {s}–{e} (tuần tự từng chương)."
    else:
        return
    running_msg = f"⏳ Running in background: **{user_request[:100]}**. {desc} Check **Background Jobs** tab for status."
    try:
        services = init_services()
        if not services:
            st.toast("Không kết nối được dịch vụ.")
            return
        supabase = services["supabase"]
        if st.session_state.get("enable_history", True):
            if insert_user_message:
                supabase.table("chat_history").insert([
                    {"story_id": project_id, "user_id": str(user_id) if user_id else None, "role": "user", "content": user_request, "created_at": now_timestamp, "metadata": {"data_operation_background": True}},
                    {"story_id": project_id, "user_id": str(user_id) if user_id else None, "role": "model", "content": running_msg, "created_at": now_timestamp, "metadata": {"data_operation_background": True}},
                ]).execute()
            else:
                supabase.table("chat_history").insert({
                    "story_id": project_id, "user_id": str(user_id) if user_id else None, "role": "model", "content": running_msg, "created_at": now_timestamp, "metadata": {"data_operation_background": True},
                }).execute()
            _after_save_history_v_work(project_id, user_id, active_persona.get("role", ""), allow_data_changing=True)
        from core.background_jobs import create_job, run_job_worker
        # steps toàn bộ là unified -> tạo 1 job unified_chapter_range (từ bước đầu)
        if steps and len(steps) and all((s.get("target") or "") == "unified" for s in steps) and (steps[0].get("chapter_range") or []):
            ur = steps[0]["chapter_range"]
            if isinstance(ur, (list, tuple)) and len(ur) >= 2:
                s, e = int(ur[0]), int(ur[1])
                s, e = min(s, e), max(s, e)
                unified_range = [s, e]
                desc = f"Unified analyze chương {s}–{e} (tuần tự từng chương)."
                steps = None
        if unified_range and isinstance(unified_range, (list, tuple)) and len(unified_range) >= 2:
            s, e = int(unified_range[0]), int(unified_range[1])
            s, e = min(s, e), max(s, e)
            label = (user_request[:200] if user_request else f"Unified chương {s}–{e}")
            job_id = create_job(
                story_id=project_id,
                user_id=user_id,
                job_type="unified_chapter_range",
                label=label,
                payload={"chapter_start": s, "chapter_end": e},
                post_to_chat=True,
            )
            if job_id:
                threading.Thread(target=run_job_worker, args=(job_id,), daemon=True).start()
        if steps:
            label = (user_request[:200] if user_request else "Data operation batch")
            job_id = create_job(
                story_id=project_id,
                user_id=user_id,
                job_type="data_operation_batch",
                label=label,
                payload={"steps": steps, "user_request": user_request or label},
                post_to_chat=False,
            )
            if job_id:
                threading.Thread(target=run_job_worker, args=(job_id,), daemon=True).start()
        elif single_op:
            from core.data_operation_jobs import run_data_operation
            threading.Thread(
                target=run_data_operation,
                kwargs={
                    "project_id": project_id,
                    "user_id": user_id,
                    "operation_type": single_op.get("operation_type", "extract"),
                    "target": single_op.get("target", "bible"),
                    "chapter_number": single_op.get("chapter_number"),
                    "user_request": user_request,
                    "post_completion_message": False,
                },
                daemon=True,
            ).start()
        st.toast("Started in background. Check Background Jobs tab for status.")
        if rerun_after:
            st.rerun()
    except Exception as e:
        st.error(f"Lỗi khi bắt đầu thao tác: {e}")


# --- V Home: lưu/load theo topic (không dùng chat_history) ---
V_HOME_CONTEXT_MESSAGES = 10


def _v_home_get_current_topic_start(user_id, project_id):
    """Lấy topic_start_at hiện tại của user cho project. Nếu chưa có thì tạo mới (now). Trả về chuỗi ISO."""
    if not user_id:
        return datetime.utcnow().isoformat()
    try:
        services = init_services()
        if not services:
            return datetime.utcnow().isoformat()
        q = services["supabase"].table("v_home_current_topic").select("topic_start_at").eq("user_id", str(user_id))
        if project_id and str(project_id).strip() not in ("", "None"):
            q = q.eq("story_id", project_id)
        else:
            q = q.is_("story_id", "null")
        r = q.limit(1).execute()
        if r.data and len(r.data) > 0:
            raw = r.data[0].get("topic_start_at")
            if raw is not None:
                return raw if isinstance(raw, str) else getattr(raw, "isoformat", lambda: str(raw))()
        now = datetime.utcnow().isoformat()
        story_id_val = project_id if (project_id and str(project_id).strip() not in ("", "None")) else None
        q = services["supabase"].table("v_home_current_topic").select("topic_start_at").eq("user_id", str(user_id))
        if story_id_val:
            q = q.eq("story_id", story_id_val)
        else:
            q = q.is_("story_id", "null")
        r = q.limit(1).execute()
        if r.data and len(r.data) > 0:
            u = services["supabase"].table("v_home_current_topic").update({"topic_start_at": now}).eq("user_id", str(user_id))
            if story_id_val:
                u.eq("story_id", story_id_val).execute()
            else:
                u.is_("story_id", "null").execute()
        else:
            row = {"user_id": str(user_id), "topic_start_at": now}
            if story_id_val:
                row["story_id"] = story_id_val
            services["supabase"].table("v_home_current_topic").insert(row).execute()
        return now
    except Exception:
        return datetime.utcnow().isoformat()


def _run_chat_response_background(
    messages,
    project_id,
    user_id,
    prompt,
    now_timestamp,
    intent,
    is_v_home,
    topic_start_at,
    model_key,
    temperature,
    max_tokens,
    persona_role,
    allow_data_changing,
):
    """Chạy LLM trong thread, ghi câu trả lời vào DB (không dùng st.*). Dùng cho chat_casual/web_search để đổi tab vẫn trả lời."""
    try:
        model = model_key or Config.DEFAULT_MODEL
        resp = AIService.call_openrouter(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=False,
        )
        full_response_text = (resp.choices[0].message.content or "").strip()
    except Exception as e:
        full_response_text = f"(Lỗi khi trả lời trong nền: {e})"
    try:
        if is_v_home and topic_start_at:
            _v_home_save_message(user_id, project_id, "model", full_response_text, topic_start_at)
        else:
            services = init_services()
            if services:
                services["supabase"].table("chat_history").insert({
                    "story_id": project_id,
                    "user_id": str(user_id) if user_id else None,
                    "role": "model",
                    "content": full_response_text,
                    "created_at": now_timestamp,
                    "metadata": {"intent": intent},
                }).execute()
                if not is_v_home:
                    _after_save_history_v_work(project_id, user_id, persona_role, allow_data_changing)
    except Exception:
        pass


def _pair_messages_newest_first(msgs):
    """Nhóm tin nhắn theo cặp (user, model), trả về danh sách cặp mới nhất trước. Trong mỗi cặp: câu hỏi trên, trả lời dưới."""
    if not msgs:
        return []
    pairs = []
    i = 0
    while i < len(msgs):
        if i + 1 < len(msgs) and msgs[i].get("role") == "user" and msgs[i + 1].get("role") == "model":
            pairs.append((msgs[i], msgs[i + 1]))
            i += 2
        else:
            pairs.append((msgs[i], None))
            i += 1
    pairs.reverse()
    return pairs


def _v_home_load_messages(user_id, project_id):
    """Lấy tin nhắn thuộc topic hiện tại của project (để hiển thị và làm context)."""
    if not user_id:
        return []
    try:
        services = init_services()
        if not services:
            return []
        topic_start = _v_home_get_current_topic_start(user_id, project_id)
        q = (
            services["supabase"]
            .table("v_home_messages")
            .select("id, role, content, created_at, topic_start_at")
            .eq("user_id", str(user_id))
        )
        if project_id and str(project_id).strip() not in ("", "None"):
            q = q.eq("story_id", project_id)
        else:
            q = q.is_("story_id", "null")
        r = q.order("created_at", desc=True).limit(200).execute()
        out = []
        for m in (r.data or []):
            ts = m.get("topic_start_at")
            ts_str = ts if isinstance(ts, str) else (getattr(ts, "isoformat", lambda: str(ts))() if ts else "")
            if ts_str == topic_start:
                out.append(m)
        out.reverse()
        return out
    except Exception:
        return []


def _v_home_reset_topic(user_id, project_id):
    """Reset topic: đặt topic_start_at = now. Tin nhắn sau chỉ thuộc topic mới."""
    if not user_id:
        return
    try:
        services = init_services()
        if not services:
            return
        now = datetime.utcnow().isoformat()
        story_id_val = project_id if (project_id and str(project_id).strip() not in ("", "None")) else None
        q = services["supabase"].table("v_home_current_topic").select("topic_start_at").eq("user_id", str(user_id))
        if story_id_val:
            q = q.eq("story_id", story_id_val)
        else:
            q = q.is_("story_id", "null")
        r = q.limit(1).execute()
        if r.data and len(r.data) > 0:
            if story_id_val:
                services["supabase"].table("v_home_current_topic").update({"topic_start_at": now}).eq("user_id", str(user_id)).eq("story_id", story_id_val).execute()
            else:
                services["supabase"].table("v_home_current_topic").update({"topic_start_at": now}).eq("user_id", str(user_id)).is_("story_id", "null").execute()
        else:
            row = {"user_id": str(user_id), "topic_start_at": now}
            if story_id_val:
                row["story_id"] = story_id_val
            services["supabase"].table("v_home_current_topic").insert(row).execute()
    except Exception:
        pass


def _v_home_save_message(user_id, project_id, role, content, topic_start_at):
    """Lưu 1 tin nhắn V Home (không ghi chat_history). Gắn với project_id (story_id)."""
    if not user_id:
        return
    try:
        services = init_services()
        if not services:
            return
        payload = {
            "user_id": str(user_id),
            "role": role,
            "content": content,
            "created_at": datetime.utcnow().isoformat(),
            "topic_start_at": topic_start_at,
        }
        if project_id and str(project_id).strip() not in ("", "None"):
            payload["story_id"] = project_id
        services["supabase"].table("v_home_messages").insert(payload).execute()
    except Exception:
        pass


def _auto_crystallize_background(project_id, user_id, persona_role):
    """Chạy ngầm: crystallize 25 tin (30 - 5) và lưu vào Bible [CHAT] (ngày-stt). Reset counter v7.1 về 0."""
    try:
        services = init_services()
        if not services:
            return
        supabase = services["supabase"]
        q = supabase.table("chat_history").select("id, role, content, created_at").eq("story_id", project_id)
        if user_id:
            q = q.eq("user_id", str(user_id))
        r = q.order("created_at", desc=True).limit(35).execute()
        data = list(r.data)[::-1] if r.data else []
        if len(data) < 25:
            return
        to_crystallize = data[:-5]
        chat_text = "\n".join([f"{m['role']}: {m['content']}" for m in to_crystallize])
        summary = RuleMiningSystem.crystallize_session(to_crystallize, persona_role)
        if not summary or summary == "NO_INFO":
            return
        today = datetime.utcnow().strftime("%Y-%m-%d")
        try:
            log_r = supabase.table("chat_crystallize_log").select("serial_in_day").eq(
                "story_id", project_id
            ).eq("user_id", str(user_id) or "").eq("crystallize_date", today).execute()
            serial = len(log_r.data) + 1 if log_r.data else 1
        except Exception:
            serial = 1
        entity_name = f"[CHAT] {today} chat-{serial}"
        payload = {
            "story_id": project_id,
            "entity_name": entity_name,
            "description": summary,
            "source_chapter": 0,
        }
        ins = supabase.table("story_bible").insert(payload).execute()
        bible_id = ins.data[0].get("id") if ins.data else None
        try:
            supabase.table("chat_crystallize_log").insert({
                "story_id": project_id,
                "user_id": str(user_id) if user_id else None,
                "crystallize_date": today,
                "serial_in_day": serial,
                "message_count": len(to_crystallize),
                "bible_entry_id": bible_id,
            }).execute()
        except Exception:
            pass
        _reset_crystallize_count(project_id, user_id)
        try:
            from ai_engine import suggest_relations
            suggestions = suggest_relations(summary, project_id)
            for s in (suggestions or []):
                if s.get("kind") == "relation":
                    try:
                        supabase.table("entity_relations").insert({
                            "source_entity_id": s["source_entity_id"],
                            "target_entity_id": s["target_entity_id"],
                            "relation_type": s.get("relation_type", "liên quan"),
                            "description": s.get("description", ""),
                            "story_id": project_id,
                        }).execute()
                    except Exception:
                        pass
        except Exception:
            pass
    except Exception as e:
        print(f"auto_crystallize_background error: {e}")


def render_chat_tab(project_id, persona, chat_mode=None):
    """Tab Chat. chat_mode: 'v_work' (dự án, persona, router, crystallize) hoặc 'v_home' (chat tự do, không context dự án)."""
    if chat_mode is None:
        chat_mode = "v_work"
    is_v_home = chat_mode == "v_home"

    st.header("🏠 V Home" if is_v_home else "🔧 V Work")

    col_chat, col_memory = st.columns([3, 1])

    user = st.session_state.get("user")
    user_id = getattr(user, "id", None) if user else None
    user_email = getattr(user, "email", None) if user else None
    can_write = bool(
        project_id
        and user_id
        and check_permission(str(user_id), user_email or "", project_id, "write")
    )
    can_request = bool(
        project_id
        and user_id
        and check_permission(str(user_id), user_email or "", project_id, "request_write")
    )

    with col_memory:
        st.write("### 🧠 Memory & Settings")
        if is_v_home:
            active_persona = {"icon": "🏠", "role": "Assistant", "core_instruction": "Bạn là trợ lý thân thiện. Trả lời ngắn gọn, hữu ích. Ngôn ngữ: ưu tiên Tiếng Việt.", "system_prompt": "", "max_tokens": 4000}
            st.session_state['enable_history'] = False
            st.caption("Chat tự do — không lưu vào DB dự án. Context = 10 tin cuối của topic.")
            if st.button("🔄 Reset topic", use_container_width=True, key=f"chat_btn_reset_topic_{chat_mode}", help="Bắt đầu topic mới: từ giờ chỉ đưa tin nhắn sau thời điểm này vào context."):
                _v_home_reset_topic(user_id, project_id)
                st.toast("Đã bắt đầu topic mới.")
        else:
            available = PersonaSystem.get_available_personas()
            default_key = st.session_state.get("persona", "Writer")
            idx = available.index(default_key) if default_key in available else 0
            selected_persona_key = st.selectbox(
                "Persona trả lời",
                available,
                index=idx,
                key=f"chat_persona_key_{chat_mode}",
                help="Chọn persona để AI trả lời theo phong cách này. Nếu câu trả lời quá cộc lốc, chỉnh system prompt của persona trong Settings sẽ giúp trả lời tự nhiên, đủ ý hơn."
            )
            active_persona = PersonaSystem.get_persona(selected_persona_key)
            st.session_state['enable_history'] = True

            if st.button("🧹 Clear Screen", use_container_width=True, key=f"chat_btn_clear_{chat_mode}"):
                st.session_state['chat_cutoff'] = datetime.utcnow().isoformat()

            if st.button("🔄 Show All", use_container_width=True, key=f"chat_btn_show_all_{chat_mode}"):
                st.session_state['chat_cutoff'] = "1970-01-01"

        if not is_v_home:
            st.session_state['strict_mode'] = st.toggle(
                "🚫 Strict Mode",
                value=st.session_state.get('strict_mode', False),
                help="ON: AI only answers based on found data. No fabrication. (Temp = 0)",
                key=f"chat_toggle_strict_{chat_mode}",
            )
            st.session_state['use_v7_planner'] = st.toggle(
                "📐 V7 Planner",
                value=st.session_state.get('use_v7_planner', False),
                help="V sẽ tư duy để tìm câu trả lời tốt nhất.",
                key=f"chat_toggle_v7_{chat_mode}",
            )
            st.session_state['allow_data_changing_actions'] = st.toggle(
                "🔧 Cho phép thao tác ảnh hưởng dữ liệu",
                value=st.session_state.get('allow_data_changing_actions', False),
                help="Chỉ áp dụng khi chat (V Work): bật mới cho phép extract/update/delete theo chương, ghi Bible/Rule, Crystallize, thêm Semantic. Tab khác (Data Analyze, Knowledge…) không bị ảnh hưởng. Mặc định tắt.",
                key=f"chat_toggle_allow_data_{chat_mode}",
            )
            st.session_state['extract_rules_from_chat'] = st.toggle(
                "🧐 Trích xuất luật từ chat",
                value=st.session_state.get('extract_rules_from_chat', False),
                help="Bật: sau mỗi câu trả lời, AI tìm luật trong hội thoại; bạn xác nhận Lưu/Bỏ qua thì ghi luật vào Bible (không cần bật toggle Cho phép thao tác ảnh hưởng dữ liệu).",
                key=f"chat_toggle_extract_rules_{chat_mode}",
            )
            st.divider()
            st.write("### 🕰️ Context cho Router / Planner")
            st.session_state["history_depth"] = st.slider(
                "Số tin nhắn cũ đưa vào Router & V7 Planner",
                min_value=0,
                max_value=50,
                value=st.session_state.get("history_depth", 5),
                step=1,
                help="Bao nhiêu tin gần nhất được đưa vào Router và V7 Planner để chọn intent và lên kế hoạch. Trả lời cuối dựa trên context từ Bible/chương đã thu thập, không nhồi thêm lịch sử chat.",
                key=f"chat_history_depth_{chat_mode}",
            )
            crystallize_count = _get_crystallize_count(project_id, user_id) if project_id and user_id else 0
            st.caption(f"💎 Crystallize: **{crystallize_count} / 30** tin (sau 30 → tóm tắt & lưu Bible [CHAT], xem tại **Knowledge > Bible** hoặc **Memory**).")
            if st.session_state.get("crystallize_blocked_no_allow"):
                st.warning("Đã đủ 30 tin để Crystallize nhưng toggle **Cho phép thao tác ảnh hưởng dữ liệu** đang tắt. Bật lên rồi gửi thêm tin để thực hiện.")
                if st.session_state.get("allow_data_changing_actions", False):
                    st.session_state.pop("crystallize_blocked_no_allow", None)
        else:
            st.session_state["history_depth"] = st.session_state.get("history_depth", 5)

    def _chat_messages_fragment():
        # Tin nhắn luôn load từ DB mỗi lần render → user đổi tab rồi quay lại vẫn thấy đầy đủ (không mất).
        # Ô chat cố định trên cùng (form thay st.chat_input để luôn nằm trên)
        form_key = "chat_form_v_home" if is_v_home else "chat_form_v_work"
        with st.form(form_key):
            prompt = st.text_input(
                "Tin nhắn",
                placeholder=f"Hỏi {active_persona['icon']} AI Assistant...",
                key=f"chat_input_field_{'v_home' if is_v_home else 'v_work'}",
                label_visibility="collapsed",
            )
            submitted = st.form_submit_button("Gửi")
        st.divider()

        if is_v_home:
            if not project_id or str(project_id).strip() in ("", "None"):
                visible_msgs = []
                st.info("Chọn một project ở sidebar để dùng V Home (tin nhắn gắn với project đó).")
            else:
                visible_msgs = _v_home_load_messages(user_id, project_id)
            # Thứ tự: cặp mới nhất lên đầu; trong mỗi cặp câu hỏi trên, trả lời dưới
            for user_msg, model_msg in _pair_messages_newest_first(visible_msgs):
                with st.chat_message("user"):
                    st.markdown(user_msg.get("content", ""))
                if model_msg:
                    with st.chat_message("model", avatar=active_persona["icon"]):
                        st.markdown(model_msg.get("content", ""))
            if visible_msgs and visible_msgs[-1].get("role") == "user":
                st.caption("⏳ Đang trả lời trong nền. Làm mới trang hoặc quay lại tab sau vài giây để thấy câu trả lời.")
        else:
            visible_msgs = []
            if not project_id or str(project_id).strip() in ("", "None"):
                st.info("Chọn một project ở sidebar để xem và gửi tin nhắn trong V Work.")
            else:
                try:
                    services = init_services()
                    supabase = services["supabase"]
                    q = (
                        supabase.table("chat_history")
                        .select("*")
                        .eq("story_id", project_id)
                    )
                    if user_id:
                        q = q.eq("user_id", str(user_id))
                    msgs_data = (
                        q.order("created_at", desc=True)
                        .limit(50)
                        .execute()
                    )
                    msgs = msgs_data.data[::-1] if msgs_data.data else []
                    visible_msgs = [m for m in msgs if m["created_at"] > st.session_state.get("chat_cutoff", "1970-01-01")]
                    # Thứ tự: cặp mới nhất lên đầu; trong mỗi cặp câu hỏi trên, trả lời dưới
                    for user_msg, model_msg in _pair_messages_newest_first(visible_msgs):
                        with st.chat_message("user"):
                            st.markdown(user_msg.get("content", ""))
                        if model_msg:
                            with st.chat_message("model", avatar=active_persona["icon"]):
                                st.markdown(model_msg.get("content", ""))
                                if model_msg.get("metadata"):
                                    with st.expander("📊 Details"):
                                        st.json(model_msg["metadata"], expanded=False)
                    if visible_msgs and visible_msgs[-1].get("role") == "user":
                        st.caption("⏳ Đang trả lời trong nền. Làm mới trang hoặc quay lại tab sau vài giây để thấy câu trả lời.")
                except Exception as e:
                    st.error(f"Error loading history: {e}")
        history_depth = st.session_state.get("history_depth", 5)
        prompt = (prompt or "").strip()
        # Cho phép prompt từ form clarification (user gõ vào ô làm rõ rồi bấm Gửi)
        prompt_from_clarification = st.session_state.pop("pending_clarification_prompt", None)
        if prompt_from_clarification:
            prompt_from_clarification = (prompt_from_clarification or "").strip()
        prompt_to_use = (prompt if (submitted and prompt) else None) or prompt_from_clarification
        from_main_form = bool(submitted and prompt)

        if prompt_to_use:
            prompt = prompt_to_use
            now_timestamp = datetime.utcnow().isoformat()
            # Ghi câu hỏi vào DB ngay để đổi tab vẫn thấy; câu trả lời sẽ ghi sau (đồng bộ hoặc trong nền).
            if st.session_state.get("enable_history", True):
                try:
                    if is_v_home and user_id and project_id:
                        topic_start = _v_home_get_current_topic_start(user_id, project_id)
                        _v_home_save_message(user_id, project_id, "user", prompt, topic_start)
                    elif not is_v_home and project_id:
                        services = init_services()
                        if services:
                            services["supabase"].table("chat_history").insert({
                                "story_id": project_id,
                                "user_id": str(user_id) if user_id else None,
                                "role": "user",
                                "content": prompt,
                                "created_at": now_timestamp,
                                "metadata": {},
                            }).execute()
                except Exception:
                    pass

            with st.chat_message("user"):
                st.markdown(prompt)

            with st.spinner("Thinking..."):
                v7_handled = False
                router_out = None
                free_chat_mode = is_v_home or st.session_state.get('free_chat_mode', False)

                # Số tin đưa vào Router/Planner theo slider (0 = không dùng lịch sử).
                depth = history_depth if not is_v_home else 0
                if depth > 0 and visible_msgs:
                    recent_history_text = "\n".join([
                        f"{m['role']}: {m['content']}"
                        for m in visible_msgs[-depth:]
                    ])
                else:
                    recent_history_text = "" if not is_v_home else "\n".join([
                        f"{m.get('role', 'user')}: {m.get('content', '')}"
                        for m in visible_msgs[-V_HOME_CONTEXT_MESSAGES:]
                    ])

                if free_chat_mode:
                    router_out = {"intent": "chat_casual", "target_files": [], "target_bible_entities": [], "rewritten_query": prompt, "chapter_range": None, "chapter_range_mode": None, "chapter_range_count": 5}
                    debug_notes = ["Intent: chat_casual", "🌐 Chat tự do"]
                else:
                    debug_notes = []
                    max_llm_calls_per_turn = Config.get_max_llm_calls_per_turn()
                    llm_calls_this_turn = [0]
                    # Chỉ lệnh @: parse trước; fallback ask_user_clarification nếu thiếu/sai (không đoán ý)
                    if not is_v_home and is_command_message(prompt):
                        parse_result = parse_command(prompt, project_id, str(user_id) if user_id else None)
                        if parse_result.status in ("incomplete", "unknown"):
                            clarification_message = get_fallback_clarification(parse_result)
                            with st.chat_message("assistant", avatar=active_persona['icon']):
                                st.caption("📌 Chỉ lệnh (@@) — cần làm rõ")
                                st.info(clarification_message)
                                with st.form("clarification_form_cmd"):
                                    followup = st.text_input("Gõ lại hoặc bổ sung rồi bấm Gửi (hoặc Enter):", key="clarification_input_cmd", placeholder="Ví dụ: @@extract_bible chương 3", label_visibility="collapsed")
                                    if st.form_submit_button("Gửi") and (followup or "").strip():
                                        st.session_state["pending_clarification_prompt"] = (followup or "").strip()
                                        st.rerun()
                            if st.session_state.get('enable_history', True):
                                try:
                                    services = init_services()
                                    supabase = services['supabase']
                                    supabase.table("chat_history").insert({
                                        "story_id": project_id,
                                        "user_id": str(user_id) if user_id else None,
                                        "role": "model",
                                        "content": f"[Cần làm rõ] {clarification_message}",
                                        "created_at": now_timestamp,
                                        "metadata": {"intent": "ask_user_clarification"},
                                    }).execute()
                                    _after_save_history_v_work(project_id, user_id, active_persona.get("role", ""), st.session_state.get("allow_data_changing_actions", False))
                                except Exception:
                                    pass
                            v7_handled = True
                        elif parse_result.status == "ok":
                            router_out = parse_result.parsed.router_out
                            debug_notes = ["📌 Chỉ lệnh", f"Intent: {parse_result.parsed.intent}"]
                    if router_out is None:
                        semantic_match = None
                        try:
                            svc = init_services()
                            if svc:
                                r = svc["supabase"].table("settings").select("value").eq("key", "semantic_intent_no_use").execute()
                                no_use = r.data and r.data[0] and int(r.data[0].get("value", 0)) == 1
                                if not no_use:
                                    semantic_match = check_semantic_intent(prompt, project_id)
                        except Exception:
                            semantic_match = check_semantic_intent(prompt, project_id)
                    if router_out is None and semantic_match:
                        router_out = {"intent": "chat_casual", "target_files": [], "target_bible_entities": [], "rewritten_query": prompt, "chapter_range": None, "chapter_range_mode": None, "chapter_range_count": 5}
                        if semantic_match.get("related_data"):
                            router_out["_semantic_data"] = semantic_match["related_data"]
                        debug_notes.append(f"🎯 Semantic match {int(semantic_match.get('similarity',0)*100)}%")
                    elif router_out is None and not is_v_home:
                        # Mô hình 3 bước (chung Router & Planner): (1) Intent (2) Plan hoặc context_planner (3) Execute + trả lời. Giới hạn LLM/turn (verification không tính).
                        can_call = max_llm_calls_per_turn == 0 or llm_calls_this_turn[0] < max_llm_calls_per_turn
                        if can_call:
                            step1 = SmartAIRouter.intent_only_classifier(prompt, recent_history_text, project_id)
                            llm_calls_this_turn[0] += 1
                        else:
                            step1 = {"intent": "chat_casual", "needs_data": False, "rewritten_query": prompt, "clarification_question": "", "relevant_rules": ""}
                        intent_step1 = step1.get("intent", "chat_casual")
                        needs_data = step1.get("needs_data", False)
                        use_v7 = st.session_state.get("use_v7_planner", False)
                        # Chỉ chạy V7 khi RÕ RÀNG cần nhiều bước: router suggest_v7 VÀ câu có cụm đa intent (tránh câu đơn bị ép V7)
                        want_multi = (intent_step1 == "suggest_v7") and is_multi_intent_request(prompt)

                        if use_v7 and want_multi:
                            can_call_plan = max_llm_calls_per_turn == 0 or llm_calls_this_turn[0] < max_llm_calls_per_turn
                            if can_call_plan:
                                plan_result = SmartAIRouter.get_plan_v7_light(prompt, recent_history_text, project_id, intent_from_step1=intent_step1)
                                llm_calls_this_turn[0] += 1
                            else:
                                plan_result = SmartAIRouter._single_intent_to_plan({
                                    "intent": intent_step1, "rewritten_query": step1.get("rewritten_query", prompt),
                                    "context_needs": [], "context_priority": [], "target_files": [], "target_bible_entities": [],
                                    "chapter_range": None, "chapter_range_mode": None, "chapter_range_count": 5,
                                    "query_target": "", "data_operation_type": "", "data_operation_target": "",
                                    "clarification_question": step1.get("clarification_question", ""), "reason": "",
                                }, prompt)
                            plan = plan_result.get("plan") or []
                            first_intent = (plan[0].get("intent", "") if plan else "") or "chat_casual"
                        else:
                            plan_result = None
                            plan = []
                            first_intent = intent_step1
                            if needs_data:
                                can_call_ctx = max_llm_calls_per_turn == 0 or llm_calls_this_turn[0] < max_llm_calls_per_turn
                                if can_call_ctx:
                                    router_out = SmartAIRouter.context_planner(
                                        prompt, intent_step1, recent_history_text, project_id,
                                        relevant_rules=step1.get("relevant_rules") or "",
                                    )
                                    llm_calls_this_turn[0] += 1
                                else:
                                    router_out = {"intent": intent_step1, "rewritten_query": step1.get("rewritten_query", prompt),
                                        "clarification_question": step1.get("clarification_question", ""),
                                        "context_needs": [], "context_priority": [], "target_files": [], "target_bible_entities": [],
                                        "chapter_range": None, "chapter_range_mode": None, "chapter_range_count": 5,
                                        "query_target": "", "data_operation_type": "", "data_operation_target": ""}
                            else:
                                router_out = {"intent": intent_step1, "rewritten_query": step1.get("rewritten_query", prompt),
                                    "clarification_question": step1.get("clarification_question", ""),
                                    "context_needs": [], "context_priority": [], "target_files": [], "target_bible_entities": [],
                                    "chapter_range": None, "chapter_range_mode": None, "chapter_range_count": 5,
                                    "query_target": "", "data_operation_type": "", "data_operation_target": ""}

                        if plan_result and plan and first_intent == "ask_user_clarification":
                            clarification_question = (plan[0].get("args") or {}).get("clarification_question", "") or "Bạn có thể nói rõ hơn câu hỏi hoặc chủ đề bạn muốn hỏi?"
                            with st.chat_message("assistant", avatar=active_persona['icon']):
                                st.caption("🧠 V7 Planner — Cần làm rõ")
                                st.info(f"**Để trả lời chính xác, tôi cần bạn làm rõ:**\n\n{clarification_question}")
                                with st.form("clarification_form_v7"):
                                    followup = st.text_input("Gõ lại hoặc bổ sung rồi bấm Gửi (hoặc Enter):", key="clarification_input_v7", placeholder="Ví dụ: Tôi muốn hỏi về nhân vật A trong chương 3", label_visibility="collapsed")
                                    if st.form_submit_button("Gửi") and (followup or "").strip():
                                        st.session_state["pending_clarification_prompt"] = (followup or "").strip()
                                        st.rerun()
                            if st.session_state.get('enable_history', True):
                                try:
                                    services = init_services()
                                    supabase = services['supabase']
                                    supabase.table("chat_history").insert({
                                        "story_id": project_id,
                                        "user_id": str(user_id) if user_id else None,
                                        "role": "model",
                                        "content": f"[Cần làm rõ] {clarification_question}",
                                        "created_at": now_timestamp,
                                        "metadata": {"intent": first_intent},
                                    }).execute()
                                    if not is_v_home:
                                        _after_save_history_v_work(project_id, user_id, active_persona.get("role", ""), st.session_state.get("allow_data_changing_actions", False))
                                except Exception:
                                    pass
                            v7_handled = True
                        elif first_intent == "update_data" and not is_v_home and can_write and (plan or []) and all((s.get("intent") or "") == "update_data" for s in (plan or [])):
                            # Chỉ xử lý "chỉ update_data" khi toàn bộ plan là update_data. Mọi thao tác theo chương đều qua Unified.
                            unified_range_v7 = None
                            for s in (plan or []):
                                if (s.get("intent") or "") != "update_data":
                                    continue
                                a = s.get("args") or {}
                                t = (a.get("data_operation_target") or "").strip()
                                ch_range = a.get("chapter_range")
                                if t not in ("unified", "") or not ch_range or not isinstance(ch_range, (list, tuple)):
                                    continue
                                try:
                                    if len(ch_range) >= 2:
                                        start, end = int(ch_range[0]), int(ch_range[1])
                                        unified_range_v7 = [min(start, end), max(start, end)]
                                    elif len(ch_range) >= 1:
                                        ch_num = int(ch_range[0])
                                        unified_range_v7 = [ch_num, ch_num]
                                except (ValueError, TypeError):
                                    pass
                                break
                            if unified_range_v7:
                                _start_data_operation_background(
                                    project_id, user_id, prompt, active_persona, now_timestamp,
                                    unified_range=unified_range_v7,
                                )
                                v7_handled = True
                        # Chỉ chạy khối V7 (execute_plan + draft + verify) khi có plan thật (từ V7 planner). Plan rỗng = đã đi router -> bỏ qua, xuống dùng router_out.
                        if not v7_handled and plan:
                            retries_used = 0
                            status_label = "V7 Multi-step"
                            _plan = plan_result or {}
                            with st.status(f"📐 {status_label}", expanded=False) as status:
                                st.write("🧠 Planning...")
                                if _plan.get("analysis"):
                                    st.caption(_plan["analysis"][:500] + ("..." if len(_plan.get("analysis", "")) > 500 else ""))
                                cumulative_context = ""
                                sources = []
                                step_results = []
                                replan_events = []
                                try:
                                    st.write(f"⚙️ Executing {len(plan)} step(s)...")
                                    cumulative_context, sources, step_results, replan_events, data_operation_steps = execute_plan(
                                        plan,
                                        project_id,
                                        active_persona,
                                        prompt,
                                        st.session_state.get('strict_mode', False),
                                        st.session_state.get('current_arc_id'),
                                        dict(st.session_state),
                                        free_chat_mode=False,
                                        max_context_tokens=Config.CONTEXT_SIZE_TOKENS.get(st.session_state.get("context_size", "medium")),
                                        run_numerical_executor=True,
                                        llm_budget_ref=llm_calls_this_turn + [max_llm_calls_per_turn],
                                    )
                                    if data_operation_steps:
                                        _start_data_operation_background(
                                            project_id, user_id, prompt, active_persona, now_timestamp,
                                            steps=data_operation_steps, insert_user_message=False, rerun_after=False,
                                        )
                                    if replan_events:
                                        for ev in replan_events:
                                            st.caption(f"🔄 Re-plan (sau step {ev.get('step_id')}): {ev.get('reason', '')[:80]}... → {ev.get('action', '')}")
                                    st.write("📝 Generating draft...")
                                    can_draft = max_llm_calls_per_turn == 0 or llm_calls_this_turn[0] < max_llm_calls_per_turn
                                    if not can_draft:
                                        draft_response = f"(Đã đạt giới hạn {max_llm_calls_per_turn} lần gọi LLM cho lượt này. Có thể tăng trong **Settings → V8 & Observability**.)"
                                    else:
                                        system_content = (active_persona.get("system_prompt") or "") + "\n\nQUY TẮC: Chỉ trả lời dựa trên CONTEXT bên dưới. Không bịa đặt, không thêm thông tin ngoài context.\n\n--- CONTEXT (Các bước đã thực thi) ---\n" + cumulative_context
                                        user_content = prompt
                                        draft_resp = AIService.call_openrouter(
                                            messages=[
                                                {"role": "system", "content": system_content},
                                                {"role": "user", "content": user_content},
                                            ],
                                            model=st.session_state.get('selected_model', Config.DEFAULT_MODEL),
                                            temperature=0.0 if st.session_state.get('strict_mode') else 0.7,
                                            max_tokens=4096,
                                            stream=False,
                                        )
                                        draft_response = (draft_resp.choices[0].message.content or "").strip()
                                        llm_calls_this_turn[0] += 1
                                    st.write("🛡️ Verifying...")
                                    # Bật Strict mode thì luôn verify để chống bịa dữ liệu
                                    verification_required = st.session_state.get("strict_mode", False) or _plan.get("verification_required", True)

                                    def _llm_generate(system_content: str, user_content: str) -> str:
                                        r = AIService.call_openrouter(
                                            messages=[
                                                {"role": "system", "content": system_content},
                                                {"role": "user", "content": user_content},
                                            ],
                                            model=st.session_state.get('selected_model', Config.DEFAULT_MODEL),
                                            temperature=0.0,
                                            max_tokens=4096,
                                            stream=False,
                                        )
                                        return (r.choices[0].message.content or "").strip()

                                    plan_for_verifier = [{"intent": r.get("intent", "chat_casual")} for r in step_results]
                                    final_response, retries_used = run_verification_loop(
                                        draft_response,
                                        cumulative_context,
                                        plan_for_verifier,
                                        step_results,
                                        _llm_generate,
                                        verification_required=verification_required,
                                    )
                                    if retries_used > 0:
                                        st.warning("⚠️ Detecting error, auto-correcting...")
                                    status.update(label=f"✅ {status_label} — Done", state="complete")
                                except Exception as ex:
                                    status.update(label=f"❌ {status_label} — Error", state="error")
                                    final_response = f"Lỗi khi chạy V7: {ex}"
                                    import traceback
                                    st.exception(ex)

                            final_response += _get_logic_reminder(project_id)
                            with st.chat_message("assistant", avatar=active_persona['icon']):
                                # Stream hiển thị câu trả lời cuối (typewriter effect)
                                _placeholder = st.empty()
                                import time
                                _chunk = 25
                                for _i in range(0, len(final_response), _chunk):
                                    _placeholder.markdown(final_response[:_i + _chunk] + "▌")
                                    time.sleep(0.02)
                                _placeholder.markdown(final_response)
                                with st.expander("📊 V7 Details", expanded=False):
                                    st.caption(f"Steps: {len(step_results)} | Verification retries: {retries_used}")
                                    if replan_events:
                                        st.caption("🔄 Re-plan: " + "; ".join([f"Step {e.get('step_id')} → {e.get('action')}" for e in replan_events]))
                                    st.json({
                                        "plan": _plan.get("plan"),
                                        "verification_required": _plan.get("verification_required"),
                                        "replan_events": replan_events,
                                    }, expanded=False)

                            if st.session_state.get('enable_history', True):
                                try:
                                    services = init_services()
                                    supabase = services['supabase']
                                    supabase.table("chat_history").insert({
                                        "story_id": project_id,
                                        "user_id": str(user_id) if user_id else None,
                                        "role": "model",
                                        "content": final_response,
                                        "created_at": now_timestamp,
                                        "metadata": {"v7": True, "verification_required": _plan.get("verification_required")},
                                    }).execute()
                                    if not is_v_home:
                                        _after_save_history_v_work(project_id, user_id, active_persona.get("role", ""), st.session_state.get("allow_data_changing_actions", False))
                                except Exception:
                                    pass
                            v7_handled = True
                    if router_out is not None:
                        debug_notes = [f"Intent: {router_out.get('intent', 'chat_casual')}"] + debug_notes

                if not v7_handled:
                    intent = router_out.get('intent', 'chat_casual')
                    targets = router_out.get('target_files', [])
                    rewritten_query = router_out.get('rewritten_query', prompt)

                    # ask_user_clarification: dừng lại, hiện popup hỏi user thay vì gọi LLM
                    if intent == "ask_user_clarification":
                        clarification_question = router_out.get("clarification_question", "") or "Bạn có thể nói rõ hơn câu hỏi hoặc chủ đề bạn muốn hỏi?"
                        with st.chat_message("assistant", avatar=active_persona['icon']):
                            st.caption("🧠 Intent: ask_user_clarification — Cần làm rõ")
                            st.info(f"**Để trả lời chính xác, tôi cần bạn làm rõ:**\n\n{clarification_question}")
                            with st.form("clarification_form_router"):
                                followup = st.text_input("Gõ lại hoặc bổ sung rồi bấm Gửi (hoặc Enter):", key="clarification_input_router", placeholder="Ví dụ: Tôi muốn hỏi về nhân vật A trong chương 3", label_visibility="collapsed")
                                if st.form_submit_button("Gửi") and (followup or "").strip():
                                    st.session_state["pending_clarification_prompt"] = (followup or "").strip()
                                    st.rerun()
                        if st.session_state.get('enable_history', True):
                            try:
                                services = init_services()
                                supabase = services['supabase']
                                supabase.table("chat_history").insert({
                                    "story_id": project_id,
                                    "user_id": str(user_id) if user_id else None,
                                    "role": "model",
                                    "content": f"[Cần làm rõ] {clarification_question}",
                                    "created_at": now_timestamp,
                                    "metadata": {"intent": intent},
                                }).execute()
                                if not is_v_home:
                                    _after_save_history_v_work(project_id, user_id, active_persona.get("role", ""), st.session_state.get("allow_data_changing_actions", False))
                            except Exception:
                                pass
                    elif intent == "suggest_v7":
                        reason = (router_out.get("reason") or "").strip()
                        with st.chat_message("assistant", avatar=active_persona['icon']):
                            st.caption("🧠 V6 — Gợi ý dùng V7 Planner")
                            st.warning(get_v7_reminder_message())
                            if reason:
                                st.caption(f"*Lý do: {reason}*")
                        if st.session_state.get('enable_history', True):
                            try:
                                services = init_services()
                                supabase = services['supabase']
                                model_msg = "Câu hỏi cần nhiều bước xử lý (nhiều intent hoặc nhiều thao tác). Vui lòng bật V7 Planner để thực hiện đủ trong một lần."
                                supabase.table("chat_history").insert({
                                    "story_id": project_id,
                                    "user_id": str(user_id) if user_id else None,
                                    "role": "model",
                                    "content": model_msg,
                                    "created_at": now_timestamp,
                                    "metadata": {"intent": intent},
                                }).execute()
                                if not is_v_home:
                                    _after_save_history_v_work(project_id, user_id, active_persona.get("role", ""), st.session_state.get("allow_data_changing_actions", False))
                            except Exception:
                                pass
                    elif intent in ("web_search", "chat_casual"):
                        # Trả lời bằng LLM chạy trong nền → user đổi tab vẫn được trả lời; câu hỏi đã lưu ở đầu block.
                        can_call = max_llm_calls_per_turn == 0 or llm_calls_this_turn[0] < max_llm_calls_per_turn
                        if not can_call:
                            full_response_text = f"(Đã đạt giới hạn {max_llm_calls_per_turn} lần gọi LLM cho lượt này. Có thể tăng trong **Settings → V8 & Observability**.)"
                            with st.chat_message("assistant", avatar=active_persona["icon"]):
                                with st.expander("📂 Cách V lấy dữ liệu / Chi tiết", expanded=False):
                                    if debug_notes:
                                        st.caption(f"🧠 {', '.join(debug_notes)}")
                                st.markdown(full_response_text)
                            if st.session_state.get("enable_history", True):
                                try:
                                    services = init_services()
                                    if services:
                                        services["supabase"].table("chat_history").insert({
                                            "story_id": project_id,
                                            "user_id": str(user_id) if user_id else None,
                                            "role": "model",
                                            "content": full_response_text,
                                            "created_at": now_timestamp,
                                            "metadata": {"intent": intent},
                                        }).execute()
                                        if not is_v_home:
                                            _after_save_history_v_work(project_id, user_id, active_persona.get("role", ""), st.session_state.get("allow_data_changing_actions", False))
                                except Exception:
                                    pass
                        else:
                            run_instruction = active_persona["core_instruction"]
                            system_content = run_instruction + "\n\n- Hữu ích, súc tích. Ưu tiên tiếng Việt.\n- Chế độ: " + (active_persona.get("role") or "assistant")
                            if intent == "web_search":
                                try:
                                    from utils.web_search import web_search as do_web_search
                                    search_text = do_web_search(router_out.get("rewritten_query") or prompt, max_results=5)
                                    system_content += "\n\n--- KẾT QUẢ TRA CỨU (Web Search) ---\n" + (search_text or "(Không có kết quả)")
                                except Exception as ex:
                                    system_content += "\n\n[Web search lỗi: " + str(ex) + ". Trả lời dựa trên kiến thức có sẵn.]"
                            messages = [
                                {"role": "system", "content": system_content},
                                {"role": "user", "content": prompt},
                            ]
                            topic_start_at = _v_home_get_current_topic_start(user_id, project_id) if is_v_home else None
                            threading.Thread(
                                target=_run_chat_response_background,
                                kwargs={
                                    "messages": messages,
                                    "project_id": project_id,
                                    "user_id": user_id,
                                    "prompt": prompt,
                                    "now_timestamp": now_timestamp,
                                    "intent": intent,
                                    "is_v_home": is_v_home,
                                    "topic_start_at": topic_start_at,
                                    "model_key": st.session_state.get("selected_model", Config.DEFAULT_MODEL),
                                    "temperature": st.session_state.get("temperature", 0.7),
                                    "max_tokens": active_persona.get("max_tokens", 4000),
                                    "persona_role": active_persona.get("role", ""),
                                    "allow_data_changing": st.session_state.get("allow_data_changing_actions", False),
                                },
                                daemon=True,
                            ).start()
                            st.info("⏳ **Đang trả lời trong nền.** Bạn có thể đổi tab; quay lại tab Chat sẽ thấy câu trả lời.")
                            if from_main_form:
                                _chat_input_key = f"chat_input_field_{'v_home' if is_v_home else 'v_work'}"
                                if _chat_input_key in st.session_state:
                                    del st.session_state[_chat_input_key]
                            st.rerun()
                    elif intent == "update_data" and not is_v_home and can_write:
                        ch_range = router_out.get("chapter_range")
                        op_target = (router_out.get("data_operation_target") or "").strip()
                        # Thao tác dữ liệu theo chương: chỉ qua Unified (1 LLM → Bible + Timeline + Chunks + Relations).
                        if ch_range and op_target in ("unified", "") and op_target != "rule":
                            if len(ch_range) >= 2:
                                start, end = int(ch_range[0]), int(ch_range[1])
                                start, end = min(start, end), max(start, end)
                                _start_data_operation_background(
                                    project_id, user_id, prompt, active_persona, now_timestamp,
                                    unified_range=[start, end],
                                )
                            else:
                                ch_num = int(ch_range[0]) if len(ch_range) >= 1 else None
                                if ch_num is None:
                                    with st.chat_message("assistant", avatar=active_persona['icon']):
                                        st.caption("🧠 Intent: update_data (Unified)")
                                        st.warning("Không xác định được chương. Vui lòng nói rõ (ví dụ: chương 1, chương 1 đến 10).")
                                else:
                                    _start_data_operation_background(
                                        project_id, user_id, prompt, active_persona, now_timestamp,
                                        unified_range=[ch_num, ch_num],
                                    )
                    else:
                        max_context_tokens = Config.CONTEXT_SIZE_TOKENS.get(st.session_state.get("context_size", "medium"))
                        exec_result = None
                        if intent == "numerical_calculation" and not free_chat_mode:
                            context_text, sources, context_tokens = ContextManager.build_context(
                                router_out, project_id, active_persona,
                                st.session_state.get('strict_mode', False),
                                current_arc_id=st.session_state.get('current_arc_id'),
                                session_state=dict(st.session_state),
                                max_context_tokens=max_context_tokens,
                            )
                            code_prompt = f"""User hỏi: "{prompt}"
Context có sẵn:
{context_text[:6000]}

Nhiệm vụ: Tạo code Python (pandas/numpy) để trả lời. Gán kết quả cuối vào biến result.
Chỉ trả về code trong block ```python ... ```, không giải thích."""
                            can_num = max_llm_calls_per_turn == 0 or llm_calls_this_turn[0] < max_llm_calls_per_turn
                            try:
                                if can_num:
                                    llm_calls_this_turn[0] += 1
                                    code_resp = AIService.call_openrouter(
                                        messages=[{"role": "user", "content": code_prompt}],
                                        model=st.session_state.get('selected_model', Config.DEFAULT_MODEL),
                                        temperature=0.1,
                                        max_tokens=2000,
                                    )
                                else:
                                    code_resp = None
                                raw = (code_resp.choices[0].message.content or "").strip() if code_resp else ""
                                import re
                                m = re.search(r'```(?:python)?\s*(.*?)```', raw, re.DOTALL) if raw else None
                                code = (m.group(1).strip() if m else raw) if raw else ""
                                if code:
                                    val, err = PythonExecutor.execute(code, result_variable="result")
                                    if err:
                                        exec_result = f"(Executor lỗi: {err})"
                                    else:
                                        exec_result = str(val) if val is not None else "null"
                                        debug_notes.append("🧮 Python Executor OK")
                            except Exception as ex:
                                exec_result = f"(Lỗi: {ex})"
                            if exec_result:
                                context_text += f"\n\n--- KẾT QUẢ TÍNH TOÁN (Python Executor) ---\n{exec_result}"

                        if is_v_home:
                            context_text = "\n".join([
                                f"{m.get('role', 'user')}: {m.get('content', '')}"
                                for m in visible_msgs[-V_HOME_CONTEXT_MESSAGES:]
                            ])
                            sources = []
                        elif exec_result is None:
                            context_text, sources, context_tokens = ContextManager.build_context(
                                router_out,
                                project_id,
                                active_persona,
                                st.session_state.get('strict_mode', False),
                                current_arc_id=st.session_state.get('current_arc_id'),
                                session_state=dict(st.session_state),
                                free_chat_mode=free_chat_mode,
                                max_context_tokens=max_context_tokens,
                            )
                            if not free_chat_mode and router_out.get("_semantic_data"):
                                context_text = f"[SEMANTIC INTENT - Data]\n{router_out['_semantic_data']}\n\n{context_text}"
                                sources.append("🎯 Semantic Intent")

                        debug_notes.extend(sources)

                        final_prompt = f"CONTEXT:\n{context_text}\n\nUSER QUERY: {prompt}"

                        run_instruction = active_persona['core_instruction']
                        run_temperature = st.session_state.get('temperature', 0.7)

                        if st.session_state.get('strict_mode') and not free_chat_mode:
                            run_temperature = 0.0

                        messages = []
                        system_message = f"""{run_instruction}

            THÔNG TIN NGỮ CẢNH (CONTEXT):
            {context_text}

            HƯỚNG DẪN:
            - Trả lời dựa trên Context nếu có.
            - Tự nhiên, đủ ý, thân thiện; tránh cộc lốc. Hữu ích, đi thẳng vào vấn đề nhưng không cắt xén quá mức.
            - Chế độ hiện tại: {active_persona['role']}
            - Ngôn ngữ: Ưu tiên Tiếng Việt (trừ khi User yêu cầu khác hoặc code).
            """

                        messages.append({"role": "system", "content": system_message})

                        # Trả lời chỉ dựa trên context đã thu thập (Bible, chương, timeline...); không nhồi lịch sử chat vào LLM.
                        messages.append({"role": "user", "content": prompt})

                        try:
                            log_chat_turn(
                                story_id=project_id,
                                user_id=str(user_id) if user_id else None,
                                intent=intent,
                                context_needs=router_out.get("context_needs") if isinstance(router_out.get("context_needs"), list) else None,
                                context_tokens=context_tokens,
                                llm_calls_count=llm_calls_this_turn[0],
                            )
                        except Exception:
                            pass

                        can_answer = max_llm_calls_per_turn == 0 or llm_calls_this_turn[0] < max_llm_calls_per_turn
                        try:
                            if not can_answer:
                                full_response_text = f"(Đã đạt giới hạn {max_llm_calls_per_turn} lần gọi LLM cho lượt này. Có thể tăng trong **Settings → V8 & Observability**.)"
                                model = st.session_state.get('selected_model', Config.DEFAULT_MODEL)
                                with st.chat_message("assistant", avatar=active_persona['icon']):
                                    with st.expander("📂 Cách V lấy dữ liệu / Chi tiết", expanded=False):
                                        if debug_notes:
                                            st.caption(f"🧠 {', '.join(debug_notes)}")
                                        if st.session_state.get('strict_mode'):
                                            st.caption("🔒 Strict Mode: ON")
                                    st.markdown(full_response_text)
                            else:
                                llm_calls_this_turn[0] += 1
                                model = st.session_state.get('selected_model', Config.DEFAULT_MODEL)

                                # search_context: không stream câu trả lời đầu — check đủ ý + fallback xong rồi mới hiển thị một lần, tránh "hiện câu ngắn rồi đổi sang câu dài"
                                is_search_context_no_stream = (
                                    not is_v_home and intent == "search_context"
                                )
                                if is_search_context_no_stream:
                                    with st.chat_message("assistant", avatar=active_persona['icon']):
                                        with st.expander("📂 Cách V lấy dữ liệu / Chi tiết", expanded=False):
                                            if debug_notes:
                                                st.caption(f"🧠 {', '.join(debug_notes)}")
                                            if st.session_state.get('strict_mode'):
                                                st.caption("🔒 Strict Mode: ON")
                                        placeholder = st.empty()
                                        placeholder.markdown("Đang tổng hợp câu trả lời...")

                                    resp = AIService.call_openrouter(
                                        messages=messages,
                                        model=model,
                                        temperature=run_temperature,
                                        max_tokens=active_persona.get('max_tokens', 4000),
                                        stream=False,
                                    )
                                    full_response_text = (resp.choices[0].message.content or "").strip()

                                    # Thẩm định đủ ý; nếu chưa đủ thì fallback đọc full content chương rồi mới hiển thị
                                    if full_response_text and not is_answer_sufficient(
                                        prompt,
                                        full_response_text,
                                        (context_text or "")[:1000],
                                        router_out.get("context_needs"),
                                    ):
                                        ch_range = router_out.get("chapter_range")
                                        start, end = None, None
                                        if ch_range and len(ch_range) >= 2:
                                            start, end = int(ch_range[0]), int(ch_range[1])
                                            start, end = min(start, end), max(start, end)
                                        if start is None or end is None:
                                            related_nums = get_related_chapter_nums(
                                                project_id, router_out.get("target_bible_entities") or []
                                            )
                                            if related_nums:
                                                start, end = min(related_nums), max(related_nums)
                                        if start is not None and end is not None:
                                            fallback_text, _ = ContextManager.load_chapters_by_range(
                                                project_id, start, end,
                                                token_limit=ContextManager.DEFAULT_CHAPTER_TOKEN_LIMIT,
                                            )
                                            if fallback_text:
                                                extended_context = (context_text or "") + "\n\n--- NỘI DUNG CHƯƠNG (FALLBACK - đọc đầy đủ để trả lời đủ ý) ---\n" + fallback_text[:8000]
                                                retry_messages = [
                                                    {"role": "system", "content": run_instruction + "\n\nTHÔNG TIN NGỮ CẢNH (CONTEXT):\n" + extended_context + "\n\nTrả lời ĐẦY ĐỦ dựa trên context, đặc biệt nội dung chương vừa bổ sung."},
                                                    {"role": "user", "content": prompt},
                                                ]
                                                try:
                                                    retry_resp = AIService.call_openrouter(
                                                        messages=retry_messages,
                                                        model=model,
                                                        temperature=run_temperature,
                                                        max_tokens=active_persona.get("max_tokens", 4000),
                                                    )
                                                    new_answer = (retry_resp.choices[0].message.content or "").strip()
                                                    if new_answer:
                                                        full_response_text = new_answer
                                                        debug_notes.append("📄 Fallback read full content")
                                                except Exception:
                                                    pass

                                    reminder = _get_logic_reminder(project_id)
                                    if reminder:
                                        full_response_text = (full_response_text or "") + reminder
                                    placeholder.markdown(full_response_text or "(Không có nội dung trả lời.)")
                                else:
                                    # Các intent khác: stream như cũ
                                    response = AIService.call_openrouter(
                                        messages=messages,
                                        model=model,
                                        temperature=run_temperature,
                                        max_tokens=active_persona.get('max_tokens', 4000),
                                        stream=True
                                    )
                                    with st.chat_message("assistant", avatar=active_persona['icon']):
                                        with st.expander("📂 Cách V lấy dữ liệu / Chi tiết", expanded=False):
                                            if debug_notes:
                                                st.caption(f"🧠 {', '.join(debug_notes)}")
                                            if st.session_state.get('strict_mode'):
                                                st.caption("🔒 Strict Mode: ON")
                                        full_response_text = ""
                                        placeholder = st.empty()
                                        for chunk in response:
                                            if chunk.choices[0].delta.content is not None:
                                                content = chunk.choices[0].delta.content
                                                full_response_text += content
                                                placeholder.markdown(full_response_text + "▌")
                                        placeholder.markdown(full_response_text)

                            input_tokens = AIService.estimate_tokens(system_message + prompt)
                            output_tokens = AIService.estimate_tokens(full_response_text)
                            cost = AIService.calculate_cost(input_tokens, output_tokens, model)

                            if 'user' in st.session_state:
                                CostManager.update_budget(st.session_state.user.id, cost)
                                try:
                                    from utils.cache_helpers import invalidate_cache
                                    invalidate_cache()
                                except Exception:
                                    pass

                            if full_response_text:
                                if is_v_home:
                                    topic_start = _v_home_get_current_topic_start(user_id, project_id)
                                    _v_home_save_message(user_id, project_id, "model", full_response_text, topic_start)
                                elif st.session_state.get('enable_history', True):
                                    services = init_services()
                                    supabase = services['supabase']
                                    supabase.table("chat_history").insert({
                                        "story_id": project_id,
                                        "user_id": str(user_id) if user_id else None,
                                        "role": "model",
                                        "content": full_response_text,
                                        "created_at": now_timestamp,
                                        "metadata": {
                                            "intent": intent,
                                            "router_output": router_out,
                                            "model": model,
                                            "temperature": run_temperature,
                                            "cost": f"${cost:.6f}",
                                            "tokens": input_tokens + output_tokens
                                        }
                                    }).execute()

                                # update_data (ghi nhớ quy tắc): lưu pending xác nhận trước khi ghi Bible (chỉ V Work; thao tác theo chương xử lý ở nhánh khác)
                                op_t = (router_out.get("data_operation_target") or "").strip()
                                if not is_v_home and intent == "update_data" and can_write and op_t not in ("bible", "relation", "timeline", "chunking", "unified"):
                                    st.session_state["pending_update_confirm"] = {
                                        "project_id": project_id,
                                        "prompt": prompt,
                                        "response": full_response_text,
                                        "update_summary": router_out.get("update_summary", ""),
                                        "user_id": user_id,
                                    }

                                # V Work: tăng counter crystallize và trigger nếu >= 30 (reset về 0 sau crystallize)
                                if not is_v_home and can_write and user_id:
                                    _after_save_history_v_work(project_id, user_id, active_persona.get("role", ""), st.session_state.get("allow_data_changing_actions", False))

                                # Trích xuất luật từ chat (chỉ khi bật toggle trong V Work)
                                if not is_v_home and can_write and st.session_state.get("extract_rules_from_chat", False):
                                    new_rules = RuleMiningSystem.extract_rules_raw(prompt, full_response_text)
                                    if new_rules:
                                        st.session_state["pending_new_rules"] = [{"content": r, "analysis": None} for r in new_rules]

                                # Gợi ý thêm Semantic Intent chỉ khi có bước search_context (AI đánh giá + tạo câu trả lời từ context)
                                if not is_v_home and can_write and intent == "search_context":
                                    try:
                                        r = init_services()["supabase"].table("settings").select("value").eq("key", "semantic_intent_no_auto_create").execute()
                                        no_auto = r.data and r.data[0] and int(r.data[0].get("value", 0)) == 1
                                    except Exception:
                                        no_auto = True
                                    if not no_auto:
                                        st.session_state["pending_semantic_add"] = {"prompt": prompt, "response": full_response_text, "context": context_text, "intent": intent}

                            elif not st.session_state.get('enable_history', True):
                                st.caption("👻 Anonymous mode: History not saved & Rule mining disabled.")

                        except Exception as e:
                            st.error(f"Generation error: {str(e)}")

            # Xóa nội dung ô chat sau khi đã gửi (chỉ khi gửi từ form chính)
            if from_main_form:
                _chat_input_key = f"chat_input_field_{'v_home' if is_v_home else 'v_work'}"
                if _chat_input_key in st.session_state:
                    del st.session_state[_chat_input_key]

    with col_chat:
        _chat_messages_fragment()

    # Offer add to Semantic Intent (chỉ V Work): popup như luật — chỉ hiện khi có mẫu mới, nút mở dialog
    if not is_v_home and "pending_semantic_add" in st.session_state and can_write:
        p = st.session_state["pending_semantic_add"]
        _use_dialog_semantic = callable(getattr(st, "dialog", None))

        def _semantic_add_dialog():
            px = st.session_state.get("pending_semantic_add")
            if not px:
                st.caption("Đã xử lý.")
                st.session_state.pop("show_semantic_dialog", None)
                if st.button("Đóng"):
                    st.rerun()
                return
            st.caption("Câu hỏi vừa rồi không phải chat phiếm. Thêm làm mẫu để lần sau khớp nhanh?")
            st.write("**Câu hỏi:**", (px.get("prompt", "") or "")[:200])
            col_a, col_b = st.columns(2)
            with col_a:
                if st.button("✅ Thêm vào Semantic", key="chat_semantic_add_btn_dialog"):
                    if not st.session_state.get("allow_data_changing_actions", False):
                        st.warning("Bật toggle **Cho phép thao tác ảnh hưởng dữ liệu** (sidebar V Work) để thêm vào Semantic Intent.")
                    else:
                        try:
                            svc = init_services()
                            if svc:
                                sb = svc["supabase"]
                                ctx = px.get("context", "") or ""
                                resp = px.get("response", "") or ""
                                related_data = (ctx.rstrip() + "\n\n--- Câu trả lời ---\n" + resp) if ctx else resp
                                payload = {"story_id": project_id, "question_sample": px.get("prompt", ""), "intent": "chat_casual", "related_data": related_data}
                                sb.table("semantic_intent").insert(payload).execute()
                        except Exception:
                            pass
                        st.session_state.pop("pending_semantic_add", None)
                        st.session_state.pop("show_semantic_dialog", None)
                        st.toast("Đã thêm vào Semantic Intent.")
                        st.rerun()
            with col_b:
                if st.button("❌ Bỏ qua", key="chat_semantic_skip_btn_dialog"):
                    st.session_state.pop("pending_semantic_add", None)
                    st.session_state.pop("show_semantic_dialog", None)
                    st.rerun()

        if st.button("🎯 **1 mẫu Semantic mới** — Bấm để thêm hoặc bỏ qua", key="open_semantic_dialog_btn"):
            st.session_state["show_semantic_dialog"] = True
            st.rerun()
        if st.session_state.get("show_semantic_dialog"):
            if _use_dialog_semantic:
                _dialog_fn = st.dialog("🎯 Thêm vào Semantic Intent?")(_semantic_add_dialog)
                _dialog_fn()
            else:
                with st.expander("🎯 Thêm vào Semantic Intent?", expanded=True):
                    _semantic_add_dialog()

    # update_data: Xác nhận cuối cùng trước khi ghi Bible / cập nhật (chỉ V Work)
    if not is_v_home and "pending_update_confirm" in st.session_state and can_write:
        pu = st.session_state["pending_update_confirm"]
        if pu.get("project_id") == project_id:
            with st.expander("✏️ Xác nhận thực hiện cập nhật?", expanded=True):
                st.caption("Bạn đã yêu cầu ghi nhớ / cập nhật dữ liệu. Chỉ thực hiện khi bạn xác nhận.")
                st.write("**Tóm tắt:**", pu.get("update_summary", "") or "(Theo nội dung AI trả lời)")
                st.write("**Nội dung sẽ ghi:**", (pu.get("response", "") or "")[:500])
                col_ok, col_no = st.columns(2)
                with col_ok:
                    if st.button("✅ Xác nhận thực hiện", key=f"update_confirm_ok_{chat_mode}"):
                        if not st.session_state.get("allow_data_changing_actions", False):
                            st.warning("Bật toggle **Cho phép thao tác ảnh hưởng dữ liệu** (sidebar V Work) để ghi vào Bible.")
                        else:
                            try:
                                services = init_services()
                                supabase = services["supabase"]
                                content_to_save = (pu.get("response", "") or pu.get("update_summary", "") or "").strip()
                                if content_to_save:
                                    payload = {
                                        "story_id": project_id,
                                        "entity_name": f"[RULE] {datetime.now().strftime('%Y%m%d_%H%M%S')}",
                                        "description": content_to_save,
                                        "source_chapter": 0,
                                    }
                                    supabase.table("story_bible").insert(payload).execute()
                                    st.toast("Đã ghi nhớ vào Bible. Bấm **Đồng bộ vector (Bible)** trong tab Bible để tạo embedding.")
                                del st.session_state["pending_update_confirm"]
                            except Exception as e:
                                st.error(f"Lỗi khi ghi: {e}")
                with col_no:
                    if st.button("❌ Hủy", key=f"update_confirm_no_{chat_mode}"):
                        del st.session_state["pending_update_confirm"]

    # Rule Mining UI (chỉ V Work): chỉ hiện khi bật tính năng VÀ có luật mới; dạng popup để tiết kiệm diện tích
    if not is_v_home and can_write:
        if 'pending_new_rule' in st.session_state and 'pending_new_rules' not in st.session_state:
            st.session_state['pending_new_rules'] = [{"content": st.session_state['pending_new_rule'], "analysis": st.session_state.get('rule_analysis')}]
            del st.session_state['pending_new_rule']
            if 'rule_analysis' in st.session_state:
                del st.session_state['rule_analysis']

    _extract_rules_on = st.session_state.get("extract_rules_from_chat", False)
    _has_pending_rules = 'pending_new_rules' in st.session_state and isinstance(st.session_state.get('pending_new_rules'), list) and len(st.session_state.get('pending_new_rules', [])) > 0
    _use_dialog = callable(getattr(st, "dialog", None))
    if not is_v_home and can_write and _extract_rules_on and _has_pending_rules:
        pending_list = st.session_state['pending_new_rules']

        def _rules_confirmation_dialog(pid, mode):
            pl = st.session_state.get('pending_new_rules') or []
            if not pl:
                st.caption("Đã xử lý hết.")
                if "show_rules_dialog" in st.session_state:
                    del st.session_state["show_rules_dialog"]
                if st.button("Đóng"):
                    st.rerun()
                return
            st.caption("Luật lưu vào **Knowledge > Bible** (prefix [RULE]). Xác nhận từng luật hoặc tất cả.")
            for i, item in enumerate(pl):
                rule_content = item.get("content") or ""
                analysis = item.get("analysis")
                rule_key = f"rule_{i}_{mode}"
                with st.container():
                    st.write(f"**Luật {i + 1}:** {rule_content[:200]}{'…' if len(rule_content) > 200 else ''}")
                    if analysis is None:
                        with st.spinner("Đang kiểm tra trùng..."):
                            item["analysis"] = RuleMiningSystem.analyze_rule_conflict(rule_content, pid)
                            analysis = item["analysis"]
                    if analysis:
                        st.info(f"**{analysis.get('status', 'NEW')}** — {analysis.get('reason', '')}")
                        similar_rules = analysis.get("similar_rules") or []
                        if similar_rules:
                            for sr in similar_rules:
                                pct = sr.get("similarity_pct", 0)
                                st.caption(f"⚠️ Nghi ngờ trùng ({pct}% giống): _{sr.get('content', '')[:150]}…_")
                        if analysis.get("status") == "CONFLICT":
                            st.warning(f"Xung đột với: {analysis.get('existing_rule_summary', '')[:200]}")
                        elif analysis.get("status") == "MERGE":
                            st.info(f"💡 Gợi ý gộp: { (analysis.get('merged_content') or '')[:200] }…")
                    col_a, col_b = st.columns(2)
                    with col_a:
                        if st.button("✅ Lưu", key=f"rule_save_one_{rule_key}"):
                            final_content = (analysis.get('merged_content') if analysis and analysis.get('status') == "MERGE" else rule_content) or rule_content
                            services = init_services()
                            supabase = services.get("supabase") if services else None
                            if supabase:
                                try:
                                    supabase.table("story_bible").insert({
                                        "story_id": pid, "entity_name": f"[RULE] {datetime.now().strftime('%Y%m%d_%H%M%S')}", "description": final_content, "source_chapter": 0
                                    }).execute()
                                    st.toast("Đã lưu luật. Bấm **Đồng bộ vector (Bible)** trong tab Bible để tạo embedding.")
                                except Exception as e:
                                    st.error(str(e))
                            pl.pop(i)
                            if not pl:
                                st.session_state.pop('pending_new_rules', None)
                                st.session_state.pop("show_rules_dialog", None)
                            st.rerun()
                    with col_b:
                        if st.button("❌ Bỏ qua", key=f"rule_ignore_one_{rule_key}"):
                            pl.pop(i)
                            if not pl:
                                st.session_state.pop('pending_new_rules', None)
                                st.session_state.pop("show_rules_dialog", None)
                            st.rerun()
                    st.divider()
            if pl:
                col_all_a, col_all_b = st.columns(2)
                with col_all_a:
                    if st.button("✅ Lưu tất cả", key=f"rule_save_all_{mode}"):
                        services = init_services()
                        supabase = services.get("supabase") if services else None
                        for it in pl:
                            rc = it.get("content") or ""
                            an = it.get("analysis")
                            fc = (an.get('merged_content') if an and an.get('status') == "MERGE" else rc) or rc
                            if supabase:
                                try:
                                    supabase.table("story_bible").insert({
                                        "story_id": pid, "entity_name": f"[RULE] {datetime.now().strftime('%Y%m%d_%H%M%S')}",
                                        "description": fc, "source_chapter": 0
                                    }).execute()
                                except Exception:
                                    pass
                        st.toast("Đã lưu tất cả luật.")
                        st.session_state.pop('pending_new_rules', None)
                        st.session_state.pop("show_rules_dialog", None)
                        st.rerun()
                with col_all_b:
                    if st.button("❌ Bỏ qua tất cả", key=f"rule_ignore_all_{mode}"):
                        st.session_state.pop('pending_new_rules', None)
                        st.session_state.pop("show_rules_dialog", None)
                        st.rerun()

        n = len(pending_list)
        if st.button(f"🧐 **{n} luật mới** cần xác nhận — Bấm để xem", key="open_rules_dialog_btn"):
            st.session_state["show_rules_dialog"] = True
            st.rerun()
        if st.session_state.get("show_rules_dialog"):
            if _use_dialog:
                _dialog_fn = st.dialog("🧐 Luật mới từ chat")(_rules_confirmation_dialog)
                _dialog_fn(project_id, chat_mode)
            else:
                with st.expander("🧐 Luật mới từ chat", expanded=True):
                    _rules_confirmation_dialog(project_id, chat_mode)
