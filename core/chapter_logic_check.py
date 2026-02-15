# core/chapter_logic_check.py - V7.7 Soát lỗi logic theo chương (5 dimensions)
"""Build context (timeline, bible, relation, chat_crystallize, rule), gọi LLM, lưu chapter_logic_issues. Khi chạy lại: issue không còn thì đánh dấu resolved."""
import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from config import init_services

from ai.service import AIService, _get_default_tool_model
from ai.context_helpers import get_mandatory_rules
from ai.utils import get_timeline_events

LOGIC_DIMENSIONS = ("timeline", "bible", "relation", "chat_crystallize", "rule")


def _get_bible_for_logic(project_id: str, include_archived: bool = False) -> List[Dict[str, Any]]:
    """Lấy story_bible cho project; mặc định loại archived (để context không gồm [CHAT] đã archive)."""
    try:
        services = init_services()
        if not services:
            return []
        supabase = services["supabase"]
        q = supabase.table("story_bible").select("*").eq("story_id", project_id)
        if not include_archived:
            q = q.or_("archived.is.null,archived.eq.false")
        r = q.order("created_at", desc=True).execute()
        return list(r.data) if r.data else []
    except Exception as e:
        print(f"_get_bible_for_logic error: {e}")
        return []


def build_logic_context_for_chapter(
    project_id: str,
    chapter_id: int,
    chapter_number: int,
    arc_id: Optional[str] = None,
    include_archived: bool = False,
) -> str:
    """
    Tạo context soát logic: timeline, bible, relation, chat_crystallize [CHAT], rule [RULE].
    Dùng chung cho Data Health soát chương và Review (Review có thể gọi hàm này).
    """
    parts = []
    try:
        services = init_services()
        if not services:
            return "(Không kết nối được dịch vụ.)"
        supabase = services["supabase"]

        # 1) Timeline: sự kiện liên quan chương này hoặc toàn dự án (rút gọn)
        events = get_timeline_events(
            project_id,
            limit=100,
            chapter_range=(chapter_number, chapter_number),
            arc_id=arc_id,
        )
        if not events:
            events = get_timeline_events(project_id, limit=80, arc_id=arc_id)
        if events:
            lines = ["[TIMELINE - Sự kiện đã thiết lập]"]
            for e in events[:80]:
                order = e.get("event_order", 0)
                title = (e.get("title") or "").strip()
                desc = (e.get("description") or "").strip()
                if len(desc) > 400:
                    desc = desc[:397] + "..."
                if title or desc:
                    lines.append(f"  • #{order}: {title} — {desc}")
            parts.append("\n".join(lines))
        else:
            parts.append("[TIMELINE] Chưa có dữ liệu sự kiện.")

        # 2) Bible (không archive) + 3) Rule + 4) Chat crystallize: từ story_bible
        bible_entries = _get_bible_for_logic(project_id, include_archived=include_archived)
        bible_lines = []
        rule_lines = []
        chat_lines = []
        for e in bible_entries[:400]:
            name = (e.get("entity_name") or "").strip()
            desc = (e.get("description") or "").strip()
            if len(desc) > 800:
                desc = desc[:797] + "..."
            if not name:
                continue
            if name.startswith("[RULE]"):
                rule_lines.append(f"  • {name}: {desc}")
            elif name.startswith("[CHAT]"):
                chat_lines.append(f"  • {name}: {desc}")
            else:
                bible_lines.append(f"  • {name}: {desc}")

        if bible_lines:
            parts.append("[BIBLE - Nhân vật / khái niệm]\n" + "\n".join(bible_lines))
        if rule_lines:
            parts.append("[RULE - Quy tắc đã lưu]\n" + "\n".join(rule_lines))
        if chat_lines:
            parts.append("[CHAT CRYSTALLIZE - Điểm nhớ từ hội thoại]\n" + "\n".join(chat_lines))

        # 5) Relations
        id_to_name = {str(e.get("id")): (e.get("entity_name") or "").strip() for e in bible_entries if e.get("id")}
        try:
            rel_res = supabase.table("entity_relations").select("*").eq("story_id", project_id).execute()
            if rel_res.data:
                rel_lines = ["[QUAN HỆ THỰC THỂ]"]
                for r in rel_res.data[:400]:
                    src_id = r.get("source_entity_id") or r.get("entity_id")
                    tgt_id = r.get("target_entity_id")
                    src_name = id_to_name.get(str(src_id), str(src_id) if src_id else "?")
                    tgt_name = id_to_name.get(str(tgt_id), str(tgt_id) if tgt_id else "?")
                    rtype = r.get("relation_type") or r.get("relation") or "liên quan"
                    rel_lines.append(f"  • {src_name} — {rtype} — {tgt_name}")
                parts.append("\n".join(rel_lines))
        except Exception:
            parts.append("[QUAN HỆ] Chưa có dữ liệu.")

    except Exception as e:
        print(f"build_logic_context_for_chapter error: {e}")
        return f"(Lỗi build context: {e})"
    return "\n\n---\n\n".join(parts) if parts else "(Không có dữ liệu tham chiếu.)"


def _parse_issues_from_llm(content: str) -> List[Dict[str, Any]]:
    """Parse JSON từ LLM: mảng { dimension, message, details? }. dimension phải thuộc LOGIC_DIMENSIONS."""
    out = []
    content = (content or "").strip()
    # Tìm JSON array trong content
    for match in re.finditer(r"\[\s*\{[^\]]*\}\s*\]", content, re.DOTALL):
        try:
            arr = json.loads(match.group(0))
            if not isinstance(arr, list):
                continue
            for item in arr:
                if not isinstance(item, dict):
                    continue
                dim = (item.get("dimension") or "").strip().lower()
                if dim not in LOGIC_DIMENSIONS:
                    continue
                msg = (item.get("message") or "").strip()
                if not msg:
                    continue
                out.append({
                    "dimension": dim,
                    "message": msg[:2000],
                    "details": item.get("details") if isinstance(item.get("details"), dict) else {},
                })
            if out:
                return out
        except json.JSONDecodeError:
            continue
    # Fallback: một object
    try:
        obj = json.loads(content)
        if isinstance(obj, dict) and "issues" in obj:
            for item in (obj["issues"] or []):
                if isinstance(item, dict):
                    dim = (item.get("dimension") or "").strip().lower()
                    if dim in LOGIC_DIMENSIONS and item.get("message"):
                        out.append({
                            "dimension": dim,
                            "message": (item.get("message") or "")[:2000],
                            "details": item.get("details") if isinstance(item.get("details"), dict) else {},
                        })
    except json.JSONDecodeError:
        pass
    return out


def run_chapter_logic_check(
    project_id: str,
    chapter_id: int,
    chapter_number: int,
    chapter_title: str,
    chapter_content: str,
    arc_id: Optional[str] = None,
    max_content_chars: int = 80000,
) -> Tuple[List[Dict[str, Any]], int, Optional[int], str]:
    """
    Chạy soát logic 1 chương. Tạo chapter_logic_checks, gọi LLM, ghi chapter_logic_issues.
    Những issue cũ (active) không còn trong kết quả mới -> đánh dấu resolved.
    Returns: (new_issues, resolved_count, check_id, error_message).
    """
    check_id = None
    try:
        services = init_services()
        if not services:
            return [], 0, None, "Không kết nối được dịch vụ."
        supabase = services["supabase"]

        # Tạo bản ghi check (running)
        ins = supabase.table("chapter_logic_checks").insert({
            "story_id": project_id,
            "chapter_id": chapter_id,
            "arc_id": arc_id,
            "status": "running",
        }).execute()
        check_id = ins.data[0]["id"] if ins.data else None
        if not check_id:
            return [], 0, None, "Không tạo được bản ghi check."

        context_ref = build_logic_context_for_chapter(
            project_id, chapter_id, chapter_number, arc_id, include_archived=False
        )
        content_slice = (chapter_content or "")[:max_content_chars]
        if len(chapter_content or "") > max_content_chars:
            content_slice += "\n\n[... (nội dung cắt bớt do giới hạn)]"

        prompt = f"""Bạn là trợ lý kiểm tra tính logic của truyện. Soát nội dung chương dưới đây với 5 nguồn tham chiếu: TIMELINE, BIBLE, RELATION, CHAT_CRYSTALLIZE, RULE.

DỮ LIỆU THAM CHIẾU (đã thiết lập trong dự án):
---
{context_ref}
---

NỘI DUNG CHƯƠNG CẦN SOÁT (Chương #{chapter_number}: {chapter_title}):
---
{content_slice}
---

YÊU CẦU: Tìm mâu thuẫn logic, điểm vô lý, plot hole: (1) Timeline: sự kiện trong chương có trái với thứ tự/ mô tả timeline đã có không? (2) Bible: nhân vật/địa điểm/khái niệm có sai lệch với định nghĩa Bible không? (3) Relation: quan hệ giữa nhân vật có đúng với entity_relations không? (4) Chat crystallize: có trái với điểm nhớ [CHAT] không? (5) Rule: có vi phạm quy tắc [RULE] không?

Trả về ĐÚNG MỘT mảng JSON, mỗi phần tử là object có key: "dimension" (một trong: timeline, bible, relation, chat_crystallize, rule), "message" (mô tả ngắn lỗi), "details" (object tùy chọn). Nếu không có lỗi, trả về mảng rỗng [].
Ví dụ: [{{"dimension": "bible", "message": "Nhân vật A trong chương được mô tả khác với Bible.", "details": {{}}}}]
Chỉ trả về JSON, không giải thích thêm."""

        try:
            response = AIService.call_openrouter(
                messages=[{"role": "user", "content": prompt}],
                model=_get_default_tool_model(),
                temperature=0.2,
                max_tokens=4000,
            )
            raw_text = (response.choices[0].message.content or "").strip() if response and response.choices else ""
        except Exception as e:
            supabase.table("chapter_logic_checks").update({
                "status": "failed",
                "error_message": str(e)[:1000],
            }).eq("id", check_id).execute()
            return [], 0, check_id, str(e)

        issues = _parse_issues_from_llm(raw_text)
        now_iso = datetime.now(timezone.utc).isoformat()

        # Lấy danh sách active hiện tại của chương này (để so sánh và resolve)
        existing = supabase.table("chapter_logic_issues").select("id, dimension, message").eq(
            "story_id", project_id
        ).eq("chapter_id", chapter_id).eq("status", "active").execute()
        existing_list = list(existing.data or [])
        new_keys = set((i["dimension"], (i["message"] or "")[:200]) for i in issues)
        resolved_count = 0
        for ex in existing_list:
            key = (ex.get("dimension"), (ex.get("message") or "")[:200])
            if key not in new_keys:
                supabase.table("chapter_logic_issues").update({
                    "status": "resolved",
                    "resolved_at": now_iso,
                }).eq("id", ex["id"]).execute()
                resolved_count += 1

        # Insert issues mới (active)
        for i in issues:
            supabase.table("chapter_logic_issues").insert({
                "story_id": project_id,
                "chapter_id": chapter_id,
                "check_id": check_id,
                "dimension": i["dimension"],
                "message": i["message"],
                "details": i.get("details") or {},
                "status": "active",
            }).execute()

        summary = f"Phát hiện {len(issues)} lỗi; đã khắc phục {resolved_count} lỗi cũ."
        supabase.table("chapter_logic_checks").update({
            "status": "completed",
            "result_summary": summary,
            "raw_llm_response": raw_text[:50000] if raw_text else None,
        }).eq("id", check_id).execute()

        return issues, resolved_count, check_id, ""
    except Exception as e:
        print(f"run_chapter_logic_check error: {e}")
        try:
            if check_id:
                supabase.table("chapter_logic_checks").update({
                    "status": "failed",
                    "error_message": str(e)[:1000],
                }).eq("id", check_id).execute()
        except Exception:
            pass
        return [], 0, None, str(e)


def get_chapter_logic_issues(
    project_id: str,
    chapter_id: Optional[int] = None,
    status_filter: Optional[str] = None,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    """Lấy danh sách chapter_logic_issues. chapter_id=None: tất cả chương. status_filter: active | resolved | None (cả hai)."""
    try:
        services = init_services()
        if not services:
            return []
        supabase = services["supabase"]
        q = supabase.table("chapter_logic_issues").select("*").eq("story_id", project_id)
        if chapter_id is not None:
            q = q.eq("chapter_id", chapter_id)
        if status_filter:
            q = q.eq("status", status_filter)
        r = q.order("created_at", desc=True).limit(limit).execute()
        return list(r.data or [])
    except Exception as e:
        print(f"get_chapter_logic_issues error: {e}")
        return []


def get_active_logic_issues_summary(project_id: str, chapter_numbers: Optional[List[int]] = None) -> List[Dict[str, Any]]:
    """
    Lấy tóm tắt lỗi logic đang active (để nhắc user trong câu trả lời).
    chapter_numbers: nếu có thì chỉ lấy lỗi thuộc các chương đó; None = tất cả.
    Returns: list of { chapter_id, chapter_number?, title?, count, messages[] } (cần join chapters để có chapter_number/title).
    """
    try:
        services = init_services()
        if not services:
            return []
        supabase = services["supabase"]
        q = supabase.table("chapter_logic_issues").select("chapter_id, message").eq(
            "story_id", project_id
        ).eq("status", "active")
        if chapter_numbers is not None and len(chapter_numbers) > 0:
            # Resolve chapter_id from chapter_number
            ch_res = supabase.table("chapters").select("id, chapter_number, title").eq(
                "story_id", project_id
            ).in_("chapter_number", chapter_numbers).execute()
            ids = [r["id"] for r in (ch_res.data or []) if r.get("id")]
            if not ids:
                return []
            q = q.in_("chapter_id", ids)
        r = q.order("created_at", desc=True).limit(100).execute()
        rows = list(r.data or [])
        # Group by chapter_id
        by_chap: Dict[int, List[str]] = {}
        for row in rows:
            cid = row.get("chapter_id")
            if cid is None:
                continue
            if cid not in by_chap:
                by_chap[cid] = []
            msg = (row.get("message") or "").strip()
            if msg and msg not in by_chap[cid]:
                by_chap[cid].append(msg[:150])
        out = []
        for cid, messages in by_chap.items():
            if not messages:
                continue
            out.append({"chapter_id": cid, "messages": messages[:5], "count": len(messages)})
        return out
    except Exception as e:
        print(f"get_active_logic_issues_summary error: {e}")
        return []
