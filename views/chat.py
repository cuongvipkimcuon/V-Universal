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
from ai.utils import infer_bible_entities_from_prompt, parse_chapter_range_from_query


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
        from core.background_jobs import create_job, ensure_background_job_runner
        # steps toàn bộ là unified -> tạo các job unified_chapter_analyze cho từng chương trong khoảng
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
            created = 0
            for ch in range(s, e + 1):
                label_ch = f"Unified chương {ch}"
                job_id = create_job(
                    story_id=project_id,
                    user_id=user_id,
                    job_type="unified_chapter_analyze",
                    label=label_ch,
                    payload={"chapter_number": ch},
                    post_to_chat=True,
                )
                if job_id:
                    created += 1
            if created > 0:
                ensure_background_job_runner()
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
                ensure_background_job_runner()
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


def _is_system_execution_message(content: str) -> bool:
    """True nếu tin nhắn liên quan thực thi hệ thống (embedding, unified, job, đồng bộ, ...) — bỏ qua khi crystallize."""
    if not content or not isinstance(content, str):
        return False
    c = content.strip().lower()
    keywords = ("đang chạy ngầm", "running in background", "đồng bộ vector", "embedding", "unified", "extract_bible", "data operation", "job_id", "đang trả lời trong nền", "chương 1 đến", "@@")
    return any(k in c for k in keywords)


def _save_rules_and_semantic_async(
    project_id,
    prompt,
    intent,
    context_text,
    full_response_text,
    new_rules,
    enable_semantic: bool,
):
    """Chạy ngầm: lưu project_rules + semantic_intent dựa trên dữ liệu LLM đã trả lời."""
    if not project_id:
        return

    def _worker():
        try:
            services = init_services()
            if not services:
                return
            sb = services.get("supabase")
            if not sb:
                return
            # Lưu rules mới (nếu có)
            cleaned_rules = []
            for r in (new_rules or []):
                s = (r or "").strip()
                if s and s not in cleaned_rules:
                    cleaned_rules.append(s)
            if cleaned_rules:
                from utils.cache_helpers import invalidate_cache as _invalidate_cache_rules

                for rule_text in cleaned_rules:
                    content = (rule_text or "").strip()
                    if not content:
                        continue
                    payload = {
                        "scope": "project",
                        "story_id": project_id,
                        "content": content,
                        "type": "Unknown",
                    }
                    try:
                        payload_with_approve = dict(payload)
                        payload_with_approve["approve"] = False
                        sb.table("project_rules").insert(payload_with_approve).execute()
                    except Exception:
                        sb.table("project_rules").insert(payload).execute()
                try:
                    _invalidate_cache_rules()
                except Exception:
                    pass

            # Lưu semantic_intent (nếu bật)
            if enable_semantic:
                try:
                    r = sb.table("settings").select("value").eq("key", "semantic_intent_no_auto_create").execute()
                    no_auto = r.data and r.data[0] and int(r.data[0].get("value", 0)) == 1
                except Exception:
                    no_auto = True
                if not no_auto:
                    try:
                        related_data = (context_text.rstrip() + "\n\n--- Câu trả lời ---\n" + (full_response_text or "")) if context_text else (full_response_text or "")
                        payload = {
                            "story_id": project_id,
                            "question_sample": (prompt or "")[:500],
                            "intent": intent or "chat_casual",
                            "related_data": related_data,
                            "approve": False,
                        }
                        sb.table("semantic_intent").insert(payload).execute()
                    except Exception:
                        pass
        except Exception:
            pass

    threading.Thread(target=_worker, daemon=True).start()


def _auto_crystallize_background(project_id, user_id, persona_role):
    """Chạy ngầm: crystallize ~25 tin; bỏ qua tin thực thi hệ thống; lưu vào chat_crystallize_entries (V8.9). Reset counter v7.1 về 0."""
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
        # Bỏ qua tin nhắn liên quan thực thi hệ thống — chỉ ưu tiên thông tin
        to_crystallize = [m for m in data[:-5] if not _is_system_execution_message(m.get("content") or "")]
        if len(to_crystallize) < 10:
            return
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
        title = f"{today} chat-{serial}"
        try:
            # Không ghi embedding ở đây — embedding chỉ được tạo khi user bấm "Đồng bộ vector (Chat Memory)" trong tab Memory.
            ins = supabase.table("chat_crystallize_entries").insert({
                "scope": "project",
                "story_id": project_id,
                "user_id": str(user_id) if user_id else None,
                "title": title,
                "description": summary,
                "message_count": len(to_crystallize),
            }).execute()
            entry_id = ins.data[0].get("id") if ins.data else None
        except Exception:
            entry_id = None
        try:
            supabase.table("chat_crystallize_log").insert({
                "story_id": project_id,
                "user_id": str(user_id) if user_id else None,
                "crystallize_date": today,
                "serial_in_day": serial,
                "message_count": len(to_crystallize),
                "crystallize_entry_id": entry_id,
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
            if st.button("🔄 Reset topic", width="stretch", key=f"chat_btn_reset_topic_{chat_mode}", help="Bắt đầu topic mới: từ giờ chỉ đưa tin nhắn sau thời điểm này vào context."):
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

            if st.button("🧹 Clear Screen", width="stretch", key=f"chat_btn_clear_{chat_mode}"):
                st.session_state['chat_cutoff'] = datetime.utcnow().isoformat()

            if st.button("🔄 Show All", width="stretch", key=f"chat_btn_show_all_{chat_mode}"):
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
            help="Chỉ áp dụng khi chat (V Work): bật mới cho phép extract/update/delete theo chương và Crystallize. Tab khác (Data Analyze, Knowledge…) không bị ảnh hưởng. Mặc định tắt.",
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
        embedding_busy = False
        if not is_v_home and project_id:
            try:
                from core.background_jobs import is_embedding_backfill_running
                embedding_busy = is_embedding_backfill_running()
            except Exception:
                embedding_busy = False
        if embedding_busy:
            st.warning("Hệ thống đang chạy đồng bộ vector (embedding) cho Bible/Chunks/Timeline/Relations/Semantic. Tạm khóa gửi chat để tránh quá tải; thử lại sau khi đồng bộ xong.")
        with st.form(form_key):
            prompt = st.text_input(
                "Tin nhắn",
                placeholder=f"Hỏi {active_persona['icon']} AI Assistant...",
                key=f"chat_input_field_{'v_home' if is_v_home else 'v_work'}",
                label_visibility="collapsed",
            )
            submitted = st.form_submit_button("Gửi", disabled=embedding_busy)
        st.divider()
        history_depth = st.session_state.get("history_depth", 5)
        prompt = (prompt or "").strip()
        prompt_from_clarification = st.session_state.pop("pending_clarification_prompt", None)
        if prompt_from_clarification:
            prompt_from_clarification = (prompt_from_clarification or "").strip()
        prompt_to_use = (prompt if (submitted and prompt) else None) or prompt_from_clarification
        from_main_form = bool(submitted and prompt)
        if embedding_busy:
            prompt_to_use = None
            from_main_form = False

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
            visible_msgs = []  # V Work: luôn khởi tạo để dùng khi vẽ history dưới cặp mới (prompt_to_use)
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
                    # V8.9: Chỉ vẽ history khi chưa gửi câu mới — khi vừa gửi thì vẽ cặp mới ở trên (sau block LLM)
                    if not prompt_to_use:
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

            st.caption("⏳ **Đang xử lý...** Vui lòng không chuyển tab.")

            # Pre-save các câu có vẻ là luật (cách tương tác / quy ước / cách trả lời) thành project_rules
            # để tránh phụ thuộc hoàn toàn vào LLM trong RuleMiningSystem.
            if not is_v_home and project_id and can_write and st.session_state.get("extract_rules_from_chat", False):
                lp_pre = (prompt or "").strip().lower()
                explicit_rule = None
                if "từ giờ, luật là" in lp_pre:
                    parts = prompt.split(":", 1)
                    explicit_rule = (parts[1] if len(parts) > 1 else prompt).strip()
                elif "hãy nhớ rằng" in lp_pre:
                    parts = prompt.split("rằng", 1)
                    explicit_rule = (parts[1] if len(parts) > 1 else prompt).strip(" :")
                # Các câu ngắn bắt đầu bằng "luôn", "từ giờ", "kể từ giờ", "từ nay", "không được", "đừng", "cấm" → coi là 1 luật nguyên câu
                if not explicit_rule:
                    tokens = lp_pre.split()
                    if tokens and tokens[0] in ("luôn", "tuon", "từ", "tu", "từnay", "từ", "ke", "kể", "không", "khong", "đừng", "dung", "cấm", "cam"):
                        # tránh những câu quá dài (câu chuyện) — chỉ coi là luật nếu độ dài vừa phải
                        if len(prompt.strip()) <= 300:
                            explicit_rule = prompt.strip()
                    elif any(kw in lp_pre for kw in ["không được", "đừng ", "cấm ", "luôn ", "hãy ", "nhớ là "]):
                        if len(prompt.strip()) <= 300:
                            explicit_rule = prompt.strip()
                if explicit_rule:
                    try:
                        services_pre = init_services()
                        sb_pre = services_pre.get("supabase") if services_pre else None
                        if sb_pre:
                            payload_pre = {
                                "scope": "project",
                                "story_id": project_id,
                                "content": explicit_rule,
                                "type": "Unknown",
                            }
                            try:
                                payload_pre_with_approve = dict(payload_pre)
                                payload_pre_with_approve["approve"] = False
                                sb_pre.table("project_rules").insert(payload_pre_with_approve).execute()
                            except Exception:
                                # Nếu cột approve không tồn tại hoặc insert lỗi với approve → thử lại không có approve
                                sb_pre.table("project_rules").insert(payload_pre).execute()
                    except Exception as _e:
                        print(f"auto_explicit_rule_insert error: {_e}")

            with st.spinner("Thinking..."):
                v7_handled = False
                router_out = None
                query_embedding_cache = None  # (canonical_text, embedding) để tái sử dụng, chỉ embed tối thiểu
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
                                st.caption("💬 Gõ lại câu lệnh ở ô chat phía trên để tiếp tục.")
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
                                    _emb = AIService.get_embedding(prompt)
                                    if _emb:
                                        query_embedding_cache = ((prompt or "").strip(), _emb)
                                    semantic_match = check_semantic_intent(prompt, project_id, query_embedding=_emb)
                        except Exception:
                            semantic_match = check_semantic_intent(prompt, project_id)
                    if router_out is None and semantic_match:
                        router_out = {"intent": "chat_casual", "target_files": [], "target_bible_entities": [], "rewritten_query": prompt, "chapter_range": None, "chapter_range_mode": None, "chapter_range_count": 5}
                        if semantic_match.get("related_data"):
                            router_out["_semantic_data"] = semantic_match["related_data"]
                        debug_notes.append(f"🎯 Semantic match {int(semantic_match.get('similarity',0)*100)}%")
                    elif router_out is None and not is_v_home:
                        # Mô hình 3 bước (chung Router & Planner): (1) Intent (2) Plan hoặc context_planner (3) Execute + trả lời. Giới hạn LLM/turn (verification không tính).
                        use_v7 = st.session_state.get("use_v7_planner", False)

                        if use_v7:
                            # Khi đã bật V7 Planner: bỏ qua bước LLM intent_only_classifier, để Planner tự quyết định intent.
                            step1 = {
                                "intent": "chat_casual",
                                "needs_data": True,
                                "rewritten_query": prompt,
                                "clarification_question": "",
                                "relevant_rules": "",
                                "new_rules": [],
                            }
                            intent_step1 = step1["intent"]
                            needs_data = step1["needs_data"]
                        else:
                            can_call = max_llm_calls_per_turn == 0 or llm_calls_this_turn[0] < max_llm_calls_per_turn
                            if can_call:
                                step1 = SmartAIRouter.intent_only_classifier(prompt, recent_history_text, project_id)
                                llm_calls_this_turn[0] += 1
                            else:
                                step1 = {
                                    "intent": "chat_casual",
                                    "needs_data": False,
                                    "rewritten_query": prompt,
                                    "clarification_question": "",
                                    "relevant_rules": "",
                                    "new_rules": [],
                                }
                            intent_step1 = step1.get("intent", "chat_casual")
                            needs_data = step1.get("needs_data", False)
                            low_prompt = (prompt or "").strip().lower()
                            # Heuristic: Câu rất ngắn, chủ yếu là than vãn cảm xúc, không nhắc tới nội dung dự án → ép về chat_casual.
                            emotion_keywords = [
                                "buồn",
                                "bùn",
                                "mệt",
                                "mệt mỏi",
                                "chán",
                                "chán nản",
                                "stress",
                                "căng thẳng",
                                "cô đơn",
                                "tuyệt vọng",
                                "nản",
                                "tụt mood",
                            ]
                            project_keywords = [
                                "chương",
                                "chapter",
                                "chap ",
                                "nhân vật",
                                "timeline",
                                "cốt truyện",
                                "plot",
                                "story",
                                "dự án",
                                "project",
                            ]
                            words = [w for w in low_prompt.split() if w]
                            is_very_short = len(low_prompt) <= 40 and len(words) <= 6
                            has_emotion = any(k in low_prompt for k in emotion_keywords)
                            mentions_project = any(k in low_prompt for k in project_keywords)
                            if is_very_short and has_emotion and not mentions_project:
                                intent_step1 = "chat_casual"
                                needs_data = False
                            # Ghi nhớ luật / ưu tiên ("từ giờ luật là", "hãy nhớ rằng", "V nghiêm khắc khi...") → chat_casual (add rule đã tách khỏi unified).
                            if intent_step1 == "unified" and any(
                                key in low_prompt
                                for key in [
                                    "từ giờ, luật là",
                                    "tu gio, luat la",
                                    "hãy nhớ rằng",
                                    "hay nho rang",
                                    "nghiêm khắc khi",
                                    "luật là",
                                ]
                            ) and not any(k in low_prompt for k in ("chương", "chapter", "chap ")):
                                intent_step1 = "chat_casual"
                                needs_data = False

                        # Nếu user đã bật V7 Planner thì luôn ưu tiên chạy V7,
                        # còn nếu không thì chỉ chạy khi RÕ RÀNG cần nhiều bước: router suggest_v7 VÀ câu có cụm đa intent.
                        if use_v7:
                            want_multi = True
                        else:
                            want_multi = (intent_step1 == "suggest_v7") and is_multi_intent_request(prompt)

                        if use_v7 and want_multi:
                            can_call_plan = max_llm_calls_per_turn == 0 or llm_calls_this_turn[0] < max_llm_calls_per_turn
                            if can_call_plan:
                                plan_result = SmartAIRouter.get_plan_v7_light(
                                    prompt,
                                    recent_history_text,
                                    project_id,
                                    intent_from_step1=intent_step1,
                                )
                                llm_calls_this_turn[0] += 1
                            else:
                                plan_result = SmartAIRouter._single_intent_to_plan(
                                    {
                                        "intent": intent_step1,
                                        "rewritten_query": step1.get("rewritten_query", prompt),
                                        "context_needs": [],
                                        "context_priority": [],
                                        "target_files": [],
                                        "target_bible_entities": [],
                                        "chapter_range": None,
                                        "chapter_range_mode": None,
                                        "chapter_range_count": 5,
                                        "query_target": "",
                                        "data_operation_type": "",
                                        "data_operation_target": "",
                                        "clarification_question": step1.get("clarification_question", ""),
                                        "reason": "",
                                    },
                                    prompt,
                                )
                            plan = plan_result.get("plan") or []
                            first_intent = (plan[0].get("intent", "") if plan else "") or "chat_casual"
                            # Hard override: nếu Planner chọn ask_user_clarification nhưng câu hỏi đã rất rõ chương/khoảng chương
                            # và mục tiêu phân tích, thì ép về intent phân tích thay vì hỏi lại user.
                            if plan and first_intent == "ask_user_clarification":
                                low_prompt_v7 = (prompt or "").strip().lower()
                                chapter_range_v7 = None
                                try:
                                    chapter_range_v7 = parse_chapter_range_from_query(prompt or "")
                                except Exception:
                                    chapter_range_v7 = None
                                has_clear_range = isinstance(chapter_range_v7, (list, tuple)) and len(chapter_range_v7) >= 1
                                analysis_keywords = [
                                    "tóm tắt",
                                    "tom tat",
                                    "phân tích",
                                    "phan tich",
                                    "logic",
                                    "mâu thuẫn",
                                    "mau thuan",
                                    "plot hole",
                                    "so sánh",
                                    "so sanh",
                                    "% thắng",
                                    "% thang",
                                    "tỷ lệ thắng",
                                    "ty le thang",
                                    "phần trăm thắng",
                                    "phan tram thang",
                                ]
                                has_analysis_goal = any(k in low_prompt_v7 for k in analysis_keywords)
                                if has_clear_range and has_analysis_goal:
                                    step0 = plan[0] or {}
                                    args0 = (step0.get("args") or {}).copy()
                                    # Chuẩn hóa chapter_range từ parser
                                    if isinstance(chapter_range_v7, (list, tuple)) and len(chapter_range_v7) >= 2:
                                        try:
                                            start_cr = int(chapter_range_v7[0])
                                            end_cr = int(chapter_range_v7[1])
                                            args0.setdefault("chapter_range", [start_cr, end_cr])
                                        except (ValueError, TypeError):
                                            pass
                                    elif isinstance(chapter_range_v7, (list, tuple)) and len(chapter_range_v7) == 1:
                                        try:
                                            ch = int(chapter_range_v7[0])
                                            args0.setdefault("chapter_range", [ch, ch])
                                        except (ValueError, TypeError):
                                            pass
                                    args0.setdefault("chapter_range_mode", "range")
                                    step0["intent"] = "multi_chapter_analysis"
                                    step0["args"] = args0
                                    plan[0] = step0
                                    first_intent = "multi_chapter_analysis"
                                    if isinstance(plan_result, dict):
                                        plan_result["plan"] = plan
                            # Bổ sung hậu xử lý: dọn clarification_question thừa và tự động điền chapter_range
                            # cho các intent phân tích khi planner bỏ trống nhưng câu hỏi đã nêu rõ khoảng chương.
                            if plan:
                                # 1) Xóa clarification_question cho mọi bước KHÔNG phải ask_user_clarification
                                for s in plan:
                                    if not isinstance(s, dict):
                                        continue
                                    intent_s = (s.get("intent") or "").strip().lower()
                                    if intent_s != "ask_user_clarification":
                                        args_s = s.get("args") or {}
                                        if isinstance(args_s, dict) and args_s.get("clarification_question"):
                                            args_s = dict(args_s)
                                            args_s["clarification_question"] = ""
                                            s["args"] = args_s
                                # 2) Nếu bước đầu là intent phân tích mà chưa có chapter_range, thử parse từ câu hỏi.
                                step0 = plan[0] or {}
                                intent0 = (step0.get("intent") or "").strip().lower()
                                args0 = (step0.get("args") or {}) if isinstance(step0.get("args"), dict) else {}
                                has_range = isinstance(args0.get("chapter_range"), (list, tuple)) and len(args0.get("chapter_range")) >= 1
                                if intent0 in ("multi_chapter_analysis", "check_chapter_logic", "search_context") and not has_range:
                                    cr_auto = None
                                    try:
                                        cr_auto = parse_chapter_range_from_query(prompt or "")
                                    except Exception:
                                        cr_auto = None
                                    if isinstance(cr_auto, (list, tuple)) and len(cr_auto) >= 1:
                                        args0 = dict(args0)
                                        try:
                                            if len(cr_auto) >= 2:
                                                start_cr = int(cr_auto[0])
                                                end_cr = int(cr_auto[1])
                                            else:
                                                ch = int(cr_auto[0])
                                                start_cr, end_cr = ch, ch
                                            args0["chapter_range"] = [start_cr, end_cr]
                                            args0.setdefault("chapter_range_mode", "range")
                                            step0["args"] = args0
                                            plan[0] = step0
                                            if isinstance(plan_result, dict):
                                                plan_result["plan"] = plan
                                        except (ValueError, TypeError):
                                            pass
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
                                router_out = {
                                    "intent": intent_step1,
                                    "rewritten_query": step1.get("rewritten_query", prompt),
                                    "clarification_question": step1.get("clarification_question", ""),
                                    "context_needs": [],
                                    "context_priority": [],
                                    "target_files": [],
                                    "target_bible_entities": [],
                                    "chapter_range": None,
                                    "chapter_range_mode": None,
                                    "chapter_range_count": 5,
                                    "query_target": "",
                                    "data_operation_type": "",
                                    "data_operation_target": "",
                                }

                        # Đính kèm các luật mới mà intent_only_classifier phát hiện (nếu có) để dùng sau khi sinh trả lời
                        if isinstance(step1, dict) and router_out is not None:
                            nr = step1.get("new_rules") or []
                            if isinstance(nr, list):
                                router_out["_new_rules_from_step1"] = nr

                        if plan_result and plan and first_intent == "ask_user_clarification":
                            clarification_question = (plan[0].get("args") or {}).get("clarification_question", "") or "Bạn có thể nói rõ hơn câu hỏi hoặc chủ đề bạn muốn hỏi?"
                            with st.chat_message("assistant", avatar=active_persona['icon']):
                                st.caption("🧠 V7 Planner — Cần làm rõ")
                                st.info(f"**Để trả lời chính xác, tôi cần bạn làm rõ:**\n\n{clarification_question}")
                                st.caption("💬 Gõ lại câu hỏi đã làm rõ ở ô chat phía trên để tiếp tục.")
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
                        elif first_intent == "unified" and not is_v_home and (plan or []) and all((s.get("intent") or "") == "unified" for s in (plan or [])):
                            # Chỉ xử lý "chỉ unified" khi toàn bộ plan là unified. Cần can_write, allow_data, chapter_range.
                            allow_data = st.session_state.get("allow_data_changing_actions", False)
                            if not can_write:
                                msg = "Bạn cần quyền ghi và bật **Cho phép thao tác ảnh hưởng dữ liệu** (sidebar V Work), đồng thời nói rõ chương (ví dụ: chương 1 đến 5)."
                                with st.chat_message("assistant", avatar=active_persona['icon']):
                                    st.caption("🧠 Intent: unified (V7)")
                                    st.warning(msg)
                                v7_handled = True
                            elif not allow_data:
                                msg = "Để chạy Unified theo chương, hãy bật nút **Cho phép thao tác ảnh hưởng dữ liệu** (sidebar V Work) và nói rõ chương hoặc khoảng chương."
                                with st.chat_message("assistant", avatar=active_persona['icon']):
                                    st.caption("🧠 Intent: unified (V7)")
                                    st.warning(msg)
                                v7_handled = True
                            else:
                                unified_range_v7 = None
                                for s in (plan or []):
                                    if (s.get("intent") or "") != "unified":
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
                                else:
                                    msg = "Vui lòng nói rõ chương hoặc khoảng chương để chạy Unified (ví dụ: chương 1, chương 1 đến 10)."
                                    with st.chat_message("assistant", avatar=active_persona['icon']):
                                        st.caption("🧠 Intent: unified (V7)")
                                        st.warning(msg)
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
                                    # Tạo summary ngắn gọn cho toàn bộ plan + kết quả từng bước để đưa vào context cuối.
                                    plan_summary_block = ""
                                    try:
                                        if plan and step_results:
                                            plan_by_step_id = {
                                                s.get("step_id"): s for s in plan if isinstance(s, dict)
                                            }
                                            lines = ["[V7 PLAN SUMMARY]"]
                                            for sr in step_results:
                                                sid = sr.get("step_id")
                                                ps = plan_by_step_id.get(sid) or {}
                                                args_ps = ps.get("args") or {}
                                                intent_sr = sr.get("intent", "")
                                                task_name = args_ps.get("task_name") or f"{intent_sr}_{sid}"
                                                output_spec = args_ps.get("output_spec") or ""
                                                eval_status = (sr.get("evaluation_status") or "ok").upper()
                                                line = f"- Step {sid} ({task_name} – {intent_sr}): {eval_status}"
                                                lines.append(line)
                                                if output_spec:
                                                    lines.append(f"  Expected: {output_spec}")
                                                reason = sr.get("evaluation_reason") or ""
                                                if reason and eval_status != "OK":
                                                    lines.append(f"  Note: {reason}")
                                            plan_summary_block = "\n".join(lines)
                                    except Exception:
                                        plan_summary_block = ""

                                    st.write("📝 Generating draft...")
                                    can_draft = max_llm_calls_per_turn == 0 or llm_calls_this_turn[0] < max_llm_calls_per_turn
                                    if not can_draft:
                                        draft_response = f"(Đã đạt giới hạn {max_llm_calls_per_turn} lần gọi LLM cho lượt này. Có thể tăng trong **Settings → V8 & Observability**.)"
                                    else:
                                        system_content = (active_persona.get("system_prompt") or "") + "\n\nQUY TẮC: Chỉ trả lời dựa trên CONTEXT bên dưới. Không bịa đặt, không thêm thông tin ngoài context.\n"
                                        style_block = ContextManager.get_rules_block_by_type(project_id, st.session_state.get("current_arc_id"), ["Style"]) if project_id else ""
                                        if style_block:
                                            system_content += "\n\n🔥 --- STYLE RULES ---\n" + style_block + "\n"
                                        if plan_summary_block:
                                            system_content += "\n\n--- V7 PLAN SUMMARY ---\n" + plan_summary_block
                                        system_content += "\n\n--- CONTEXT (Các bước đã thực thi) ---\n" + cumulative_context
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
                    # Nếu router trả về unified nhưng user không có quyền ghi hoặc chưa bật nút ghi data,
                    # hạ unified -> search_context để vẫn trả lời Q&A bình thường theo context.
                    if router_out is not None:
                        allow_data_flag = st.session_state.get("allow_data_changing_actions", False)
                        if router_out.get("intent") == "unified" and (not can_write or not allow_data_flag):
                            router_out["intent"] = "search_context"
                            if not router_out.get("context_needs"):
                                router_out["context_needs"] = ["bible", "relation", "chapter", "timeline", "chunk"]

                    intent = router_out.get('intent', 'chat_casual')
                    targets = router_out.get('target_files', [])
                    rewritten_query = router_out.get('rewritten_query', prompt)
                    # Guard nhẹ: nếu planner/router chưa trả target_bible_entities mà intent cần data thì suy đoán từ prompt
                    if (
                        router_out is not None
                        and project_id
                        and intent in ("search_context", "query_Sql")
                    ):
                        tb = router_out.get("target_bible_entities") or []
                        if not tb:
                            inferred = infer_bible_entities_from_prompt(project_id, prompt)
                            if inferred:
                                router_out["target_bible_entities"] = list(dict.fromkeys(inferred))

                    # ask_user_clarification: dừng lại, hiện popup hỏi user thay vì gọi LLM
                    if intent == "ask_user_clarification":
                        clarification_question = router_out.get("clarification_question", "") or "Bạn có thể nói rõ hơn câu hỏi hoặc chủ đề bạn muốn hỏi?"
                        with st.chat_message("assistant", avatar=active_persona['icon']):
                            st.caption("🧠 Intent: ask_user_clarification — Cần làm rõ")
                            st.info(f"**Để trả lời chính xác, tôi cần bạn làm rõ:**\n\n{clarification_question}")
                            st.caption("💬 Gõ lại phiên bản đã làm rõ ở ô chat phía trên, rồi bấm Gửi.")
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
                        # V8.9: Không chạy ngầm — khóa màn hình, streaming, có lời nhắc không chuyển tab.
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
                            model = st.session_state.get("selected_model", Config.DEFAULT_MODEL)
                            run_temperature = st.session_state.get("temperature", 0.7)
                            max_tokens = active_persona.get("max_tokens", 4000)
                            full_response_text = ""
                            with st.chat_message("assistant", avatar=active_persona["icon"]):
                                if debug_notes:
                                    st.caption(f"🧠 {', '.join(debug_notes)}")
                                placeholder = st.empty()
                                try:
                                    response = AIService.call_openrouter(
                                        messages=messages,
                                        model=model,
                                        temperature=run_temperature,
                                        max_tokens=max_tokens,
                                        stream=True,
                                    )
                                    for chunk in response:
                                        if chunk.choices and chunk.choices[0].delta.content is not None:
                                            full_response_text += chunk.choices[0].delta.content
                                            placeholder.markdown(full_response_text + "▌")
                                    placeholder.markdown(full_response_text or "(Không có nội dung.)")
                                except Exception as e:
                                    full_response_text = f"(Lỗi: {e})"
                                    placeholder.markdown(full_response_text)
                            if st.session_state.get("enable_history", True):
                                try:
                                    if is_v_home:
                                        topic_start_at = _v_home_get_current_topic_start(user_id, project_id)
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
                                            _after_save_history_v_work(project_id, user_id, active_persona.get("role", ""), st.session_state.get("allow_data_changing_actions", False))
                                except Exception:
                                    pass
                    elif intent == "unified" and not is_v_home:
                        # Nhánh unified: chỉ thao tác theo chương (Bible + Timeline + Chunks + Relations). Cần bật nút ghi dữ liệu và có chapter_range.
                        ch_range = router_out.get("chapter_range")
                        allow_data = st.session_state.get("allow_data_changing_actions", False)
                        if not can_write:
                            msg = "Bạn cần quyền ghi để chạy Unified. Nếu đã có quyền, hãy bật nút **Cho phép thao tác ảnh hưởng dữ liệu** (sidebar V Work) và nói rõ chương (ví dụ: chương 1 đến 5)."
                            with st.chat_message("assistant", avatar=active_persona['icon']):
                                st.caption("🧠 Intent: unified")
                                st.warning(msg)
                            if st.session_state.get("enable_history", True):
                                try:
                                    services = init_services()
                                    supabase = services["supabase"]
                                    supabase.table("chat_history").insert({
                                        "story_id": project_id, "user_id": str(user_id) if user_id else None, "role": "model",
                                        "content": msg, "created_at": now_timestamp, "metadata": {"intent": "unified"},
                                    }).execute()
                                    _after_save_history_v_work(project_id, user_id, active_persona.get("role", ""), False)
                                except Exception:
                                    pass
                        elif not allow_data:
                            msg = "Để chạy Unified theo chương, hãy bật nút **Cho phép thao tác ảnh hưởng dữ liệu** (sidebar V Work). Sau đó nói rõ chương hoặc khoảng chương (ví dụ: chương 1, chương 1 đến 10)."
                            with st.chat_message("assistant", avatar=active_persona['icon']):
                                st.caption("🧠 Intent: unified")
                                st.warning(msg)
                            if st.session_state.get("enable_history", True):
                                try:
                                    services = init_services()
                                    supabase = services["supabase"]
                                    supabase.table("chat_history").insert({
                                        "story_id": project_id, "user_id": str(user_id) if user_id else None, "role": "model",
                                        "content": msg, "created_at": now_timestamp, "metadata": {"intent": "unified"},
                                    }).execute()
                                    _after_save_history_v_work(project_id, user_id, active_persona.get("role", ""), False)
                                except Exception:
                                    pass
                        elif not ch_range or not isinstance(ch_range, (list, tuple)) or len(ch_range) < 1:
                            msg = "Vui lòng nói rõ chương hoặc khoảng chương để chạy Unified (ví dụ: chương 1, chương 1 đến 10)."
                            with st.chat_message("assistant", avatar=active_persona['icon']):
                                st.caption("🧠 Intent: unified")
                                st.warning(msg)
                            if st.session_state.get("enable_history", True):
                                try:
                                    services = init_services()
                                    supabase = services["supabase"]
                                    supabase.table("chat_history").insert({
                                        "story_id": project_id, "user_id": str(user_id) if user_id else None, "role": "model",
                                        "content": msg, "created_at": now_timestamp, "metadata": {"intent": "unified"},
                                    }).execute()
                                    _after_save_history_v_work(project_id, user_id, active_persona.get("role", ""), allow_data)
                                except Exception:
                                    pass
                        else:
                            if len(ch_range) >= 2:
                                start, end = int(ch_range[0]), int(ch_range[1])
                                start, end = min(start, end), max(start, end)
                                _start_data_operation_background(
                                    project_id, user_id, prompt, active_persona, now_timestamp,
                                    unified_range=[start, end],
                                )
                            else:
                                ch_num = int(ch_range[0]) if len(ch_range) >= 1 else None
                                if ch_num is not None:
                                    _start_data_operation_background(
                                        project_id, user_id, prompt, active_persona, now_timestamp,
                                        unified_range=[ch_num, ch_num],
                                    )
                                else:
                                    msg = "Không xác định được chương. Vui lòng nói rõ (ví dụ: chương 1, chương 1 đến 10)."
                                    with st.chat_message("assistant", avatar=active_persona['icon']):
                                        st.caption("🧠 Intent: unified")
                                        st.warning(msg)
                                    if st.session_state.get("enable_history", True):
                                        try:
                                            services = init_services()
                                            supabase = services["supabase"]
                                            supabase.table("chat_history").insert({
                                                "story_id": project_id, "user_id": str(user_id) if user_id else None, "role": "model",
                                                "content": msg, "created_at": now_timestamp, "metadata": {"intent": "unified"},
                                            }).execute()
                                            _after_save_history_v_work(project_id, user_id, active_persona.get("role", ""), allow_data)
                                        except Exception:
                                            pass
                    else:
                        max_context_tokens = Config.CONTEXT_SIZE_TOKENS.get(st.session_state.get("context_size", "medium"))
                        exec_result = None
                        context_parts_meta = []
                        # Embed câu hỏi tối đa 1 lần: dùng cache nếu đã embed cho semantic, không thì embed canonical (ưu tiên prompt gốc, chỉ dùng rewritten_query khi gần giống)
                        rq = (router_out.get("rewritten_query") or "").strip() if router_out else ""
                        base_prompt = (prompt or "").strip()
                        canonical_query = base_prompt
                        if rq and base_prompt:
                            rq_low = rq.lower()
                            p_low = base_prompt.lower()
                            overlap = len(set(rq_low.split()) & set(p_low.split()))
                            if overlap >= 2:
                                canonical_query = rq
                        if not canonical_query:
                            canonical_query = rq or base_prompt
                        if query_embedding_cache and query_embedding_cache[0] == canonical_query:
                            query_embedding_for_context = query_embedding_cache[1]
                        else:
                            query_embedding_for_context = AIService.get_embedding(canonical_query) if canonical_query else None
                        if intent == "numerical_calculation" and not free_chat_mode:
                            context_text, sources, context_tokens, context_parts_meta = ContextManager.build_context(
                                router_out, project_id, active_persona,
                                st.session_state.get('strict_mode', False),
                                current_arc_id=st.session_state.get('current_arc_id'),
                                session_state=dict(st.session_state),
                                max_context_tokens=max_context_tokens,
                                query_embedding=query_embedding_for_context,
                            )
                            context_parts_meta = context_parts_meta or []
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
                                    raw = ""
                                    if code_resp and getattr(code_resp, "choices", None) and len(code_resp.choices) > 0:
                                        raw = (code_resp.choices[0].message.content or "").strip()
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
                            context_text, sources, context_tokens, context_parts_meta = ContextManager.build_context(
                                router_out,
                                project_id,
                                active_persona,
                                st.session_state.get('strict_mode', False),
                                current_arc_id=st.session_state.get('current_arc_id'),
                                session_state=dict(st.session_state),
                                free_chat_mode=free_chat_mode,
                                max_context_tokens=max_context_tokens,
                                query_embedding=query_embedding_for_context,
                            )
                            context_parts_meta = context_parts_meta or []
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

                                    # Thẩm định đủ ý; nếu chưa đủ thì fallback đọc full content chương (ưu tiên chương trong câu hỏi + chương được chunk/bible/timeline/relation nhắc nhiều).
                                    # Nếu context đã có full chương (chapter_full) thì bỏ qua bước check + fallback để tiết kiệm chi phí LLM.
                                    has_chapter_full = False
                                    meta = context_parts_meta
                                    if isinstance(meta, list) and meta:
                                        for p in meta:
                                            src = (p.get("source") or "").strip().lower()
                                            if src == "chapter_full":
                                                text_meta = (p.get("text") or "").strip()
                                                if text_meta:
                                                    has_chapter_full = True
                                                    break

                                    if full_response_text and (not has_chapter_full) and not is_answer_sufficient(
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
                                        # Thêm chương xuất hiện nhiều trong context (chunk, bible, timeline, relation)
                                        if isinstance(meta, list) and meta:
                                            from collections import Counter
                                            cnt = Counter()
                                            for p in meta:
                                                src = (p.get("source") or "").strip().lower()
                                                if src in ("chunk", "bible", "timeline", "relation"):
                                                    for num in p.get("chapter_numbers") or []:
                                                        try:
                                                            cnt[int(num)] += 1
                                                        except (TypeError, ValueError):
                                                            pass
                                            if cnt:
                                                extra = [n for n, _ in cnt.most_common(5)]
                                                all_nums = set(extra)
                                                if start is not None:
                                                    all_nums.add(start)
                                                if end is not None:
                                                    all_nums.add(end)
                                                if all_nums:
                                                    start = min(all_nums) if start is None else min(start, min(all_nums))
                                                    end = max(all_nums) if end is None else max(end, max(all_nums))
                                        if start is not None and end is not None:
                                            fallback_text, _ = ContextManager.load_chapters_by_range(
                                                project_id, start, end,
                                                token_limit=ContextManager.DEFAULT_CHAPTER_TOKEN_LIMIT,
                                            )
                                            if fallback_text:
                                                # Bỏ CHUNK, BIBLE, TIMELINE, RELATION của các chương sắp load ra khỏi context để nhẹ
                                                chapters_to_load = set(range(start, end + 1))
                                                strip_sources = {"chunk", "bible", "timeline", "relation"}
                                                base_parts = []
                                                if meta:
                                                    for p in meta:
                                                        src = (p.get("source") or "").strip().lower()
                                                        nums = set()
                                                        for n in (p.get("chapter_numbers") or []):
                                                            try:
                                                                nums.add(int(n))
                                                            except (TypeError, ValueError):
                                                                pass
                                                        if src in strip_sources and nums and (nums & chapters_to_load):
                                                            continue
                                                        base_parts.append(p.get("text") or "")
                                                else:
                                                    base_parts = [context_text or ""]
                                                base_context = "\n\n".join(p for p in base_parts if (p or "").strip())
                                                extended_context = (base_context or "") + "\n\n--- NỘI DUNG CHƯƠNG (FALLBACK - đọc đầy đủ để trả lời đủ ý) ---\n" + fallback_text[:8000]
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
                                # Trích xuất luật & Semantic Intent từ chat (auto lưu; chỉ khi bật tính năng và user có quyền ghi)
                                auto_new_rules = []
                                auto_new_semantic = False
                                new_rules_for_bg = []
                                enable_semantic_bg = False
                                if not is_v_home and can_write:
                                    # Luật: dựa trên toggle extract_rules_from_chat (trường hợp luật implicit/không theo cú pháp cố định)
                                    if st.session_state.get("extract_rules_from_chat", False):
                                        # Dùng luôn new_rules từ bước 1 (intent_only_classifier) để tránh gọi thêm LLM
                                        raw_from_step1 = []
                                        if isinstance(router_out, dict):
                                            raw_from_step1 = router_out.get("_new_rules_from_step1") or []

                                        new_rules = []
                                        if isinstance(raw_from_step1, list):
                                            for r in raw_from_step1:
                                                s = (r or "").strip()
                                                if s and s not in new_rules:
                                                    new_rules.append(s)

                                        if new_rules:
                                            auto_new_rules = new_rules
                                            new_rules_for_bg = list(new_rules)

                                    # Semantic Intent: chỉ khi semantic_intent_no_auto_create = 0
                                    try:
                                        r = init_services()["supabase"].table("settings").select("value").eq("key", "semantic_intent_no_auto_create").execute()
                                        no_auto = r.data and r.data[0] and int(r.data[0].get("value", 0)) == 1
                                    except Exception:
                                        no_auto = True
                                    if not no_auto:
                                        auto_new_semantic = True
                                        enable_semantic_bg = True

                                # Chạy luồng lưu rules + semantic_intent ở background, không chặn việc hiển thị câu trả lời
                                if (new_rules_for_bg or enable_semantic_bg) and not is_v_home and can_write:
                                    try:
                                        _save_rules_and_semantic_async(
                                            project_id,
                                            prompt,
                                            intent or "chat_casual",
                                            context_text or "",
                                            full_response_text or "",
                                            new_rules_for_bg,
                                            enable_semantic_bg,
                                        )
                                    except Exception:
                                        pass

                                # Gắn nhãn thông tin về số lượng luật / semantic mới vào router_out và append ghi chú vào cuối nội dung trả lời
                                if (auto_new_rules or auto_new_semantic) and isinstance(router_out, dict):
                                    router_out = dict(router_out)
                                    router_out["_auto_rule_count"] = len(auto_new_rules)
                                    router_out["_auto_semantic_created"] = bool(auto_new_semantic)
                                if auto_new_rules or auto_new_semantic:
                                    note_parts = []
                                    if auto_new_rules:
                                        note_parts.append("phát hiện một số **luật** mới; vào tab **Rules** để kiểm duyệt trước khi áp dụng")
                                    if auto_new_semantic:
                                        note_parts.append("tạo thêm **Semantic Intent** mới; vào tab **Semantic Intent** để kiểm duyệt")
                                    note_suffix = "\n\n> 🔎 Ghi chú hệ thống: " + " và ".join(note_parts) + "."
                                    full_response_text = (full_response_text or "") + note_suffix

                                if is_v_home:
                                    topic_start = _v_home_get_current_topic_start(user_id, project_id)
                                    _v_home_save_message(user_id, project_id, "model", full_response_text, topic_start)
                                elif st.session_state.get('enable_history', True):
                                    services = init_services()
                                    supabase = services['supabase']
                                    metadata = {
                                        "intent": intent,
                                        "router_output": router_out,
                                        "model": model,
                                        "temperature": run_temperature,
                                        "cost": f"${cost:.6f}",
                                        "tokens": input_tokens + output_tokens,
                                    }
                                    if auto_new_rules:
                                        metadata["_auto_rule_count"] = len(auto_new_rules)
                                    if auto_new_semantic:
                                        metadata["_auto_semantic_created"] = bool(auto_new_semantic)
                                    supabase.table("chat_history").insert({
                                        "story_id": project_id,
                                        "user_id": str(user_id) if user_id else None,
                                        "role": "model",
                                        "content": full_response_text,
                                        "created_at": now_timestamp,
                                        "metadata": metadata,
                                    }).execute()

                                # V Work: tăng counter crystallize và trigger nếu >= 30 (reset về 0 sau crystallize)
                                if not is_v_home and can_write and user_id:
                                    _after_save_history_v_work(project_id, user_id, active_persona.get("role", ""), st.session_state.get("allow_data_changing_actions", False))

                            elif not st.session_state.get('enable_history', True):
                                st.caption("👻 Anonymous mode: History not saved & Rule mining disabled.")

                        except Exception as e:
                            st.error(f"Generation error: {str(e)}")

            # V8.9: Câu mới nhất ở trên — vẽ lịch sử bên dưới sau khi đã vẽ cặp (user, model) mới
            if not is_v_home and visible_msgs:
                st.divider()
                for user_msg, model_msg in _pair_messages_newest_first(visible_msgs):
                    with st.chat_message("user"):
                        st.markdown(user_msg.get("content", ""))
                    if model_msg:
                        with st.chat_message("model", avatar=active_persona["icon"]):
                            md = model_msg.get("metadata") or {}
                            auto_rule_count = int(md.get("_auto_rule_count") or 0)
                            auto_semantic = bool(md.get("_auto_semantic_created", False))
                            st.markdown(model_msg.get("content", ""))
                            if auto_rule_count or auto_semantic:
                                note_parts = []
                                if auto_rule_count:
                                    note_parts.append(f"phát hiện **{auto_rule_count}** luật mới (chưa duyệt)")
                                if auto_semantic:
                                    note_parts.append("lưu **1** Semantic Intent mới (chưa duyệt)")
                                st.caption("🔎 " + "; ".join(note_parts) + " — vào tab **Rules** / **Semantic Intent** để xem và duyệt.")
                            if md:
                                with st.expander("📊 Details"):
                                    st.json(md, expanded=False)

            # Xóa nội dung ô chat sau khi đã gửi (chỉ khi gửi từ form chính)
            if from_main_form:
                _chat_input_key = f"chat_input_field_{'v_home' if is_v_home else 'v_work'}"
                if _chat_input_key in st.session_state:
                    del st.session_state[_chat_input_key]

    with col_chat:
        _chat_messages_fragment()

    # Không còn expander riêng cho Semantic Intent / Rules trong V Work chat.
