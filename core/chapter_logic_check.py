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
from core.arc_service import ArcService

LOGIC_DIMENSIONS = ("timeline", "bible", "relation", "chat_crystallize", "rule")

# Chỉ lấy rule type Info và Unknown cho soát logic (không lấy Style, Method)
RULE_TYPES_FOR_LOGIC = ("Info", "Unknown")

# max_tokens tối đa cho tool soát lỗi chất lượng (output dài để báo chi tiết)
LOGIC_CHECK_MAX_TOKENS = 128000


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


def _get_chapter_scope_for_logic(project_id: str, arc_id: Optional[str]) -> Dict[str, Any]:
    """
    Phạm vi chương cho soát logic: từ chương mục tiêu và các chương thuộc arc + toàn bộ sequential của arc.
    Nếu chương mục tiêu không có arc → toàn dự án.
    Returns: {"chapter_ids": [...], "chapter_numbers": set(...), "arc_ids": [...]}
    """
    try:
        services = init_services()
        if not services:
            return {"chapter_ids": [], "chapter_numbers": set(), "arc_ids": []}
        supabase = services["supabase"]
        arc_ids = ArcService.get_arc_ids_in_scope(project_id, arc_id)
        if not arc_ids:
            # Không có arc → toàn dự án: lấy tất cả chapter
            ch_res = supabase.table("chapters").select("id, chapter_number").eq("story_id", project_id).execute()
        else:
            ch_res = (
                supabase.table("chapters")
                .select("id, chapter_number")
                .eq("story_id", project_id)
                .in_("arc_id", list(arc_ids))
                .execute()
            )
        rows = ch_res.data or []
        chapter_ids = [r["id"] for r in rows if r.get("id") is not None]
        chapter_numbers = {int(r["chapter_number"]) for r in rows if r.get("chapter_number") is not None}
        return {"chapter_ids": chapter_ids, "chapter_numbers": chapter_numbers, "arc_ids": arc_ids}
    except Exception as e:
        print(f"_get_chapter_scope_for_logic error: {e}")
        return {"chapter_ids": [], "chapter_numbers": set(), "arc_ids": []}


def build_logic_context_for_chapter(
    project_id: str,
    chapter_id: int,
    chapter_number: int,
    arc_id: Optional[str] = None,
    include_archived: bool = False,
    dimensions: Optional[List[str]] = None,
) -> str:
    """
    Tạo context soát logic: timeline, bible, relation, chat_crystallize [CHAT], rule [RULE].
    dimensions: None hoặc rỗng = tất cả; nếu có thì chỉ gồm các dimension trong list (timeline, bible, relation, chat_crystallize, rule).
    """
    want_all = not dimensions or len(dimensions) == 0
    want = set((d or "").strip().lower() for d in (dimensions or [])) if dimensions else set(LOGIC_DIMENSIONS)
    if want_all:
        want = set(LOGIC_DIMENSIONS)

    parts = []
    try:
        services = init_services()
        if not services:
            return "(Không kết nối được dịch vụ.)"
        supabase = services["supabase"]
        scope = _get_chapter_scope_for_logic(project_id, arc_id)
        chapter_ids = scope.get("chapter_ids") or []
        chapter_numbers = scope.get("chapter_numbers") or set()
        arc_ids = scope.get("arc_ids") or []

        # 1) Timeline: chỉ chương mục tiêu + các chương thuộc arc và sequential; không arc thì toàn dự án
        if "timeline" in want:
            if chapter_ids:
                q = (
                    supabase.table("timeline_events")
                    .select("id, event_order, title, description, chapter_id")
                    .eq("story_id", project_id)
                    .in_("chapter_id", chapter_ids[:500])
                    .order("event_order")
                )
                r = q.limit(100).execute()
                events = list(r.data or [])
            else:
                events = get_timeline_events(project_id, limit=100)
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

        # 2) Bible + 3) Rule + 4) Chat crystallize: Bible/rule lọc theo scope chương + arc; rule chỉ type Info & Unknown
        if "bible" in want or "rule" in want or "chat_crystallize" in want:
            bible_entries = _get_bible_for_logic(project_id, include_archived=include_archived)
            bible_lines = []
            rule_lines = []
            chat_lines = []
            # Bible: chỉ entries có source_chapter thuộc scope (chương mục tiêu + sequential arc); không arc thì toàn bộ
            for e in bible_entries[:400]:
                name = (e.get("entity_name") or "").strip()
                desc = (e.get("description") or "").strip()
                if len(desc) > 800:
                    desc = desc[:797] + "..."
                if not name:
                    continue
                if name.startswith("[RULE]"):
                    if "rule" in want:
                        rule_lines.append(f"  • {name}: {desc}")
                elif name.startswith("[CHAT]"):
                    if "chat_crystallize" in want:
                        chat_lines.append(f"  • {name}: {desc}")
                else:
                    if "bible" in want:
                        sc = e.get("source_chapter")
                        if sc is not None:
                            try:
                                sc = int(sc)
                            except (TypeError, ValueError):
                                sc = None
                        if arc_ids:
                            # Scope theo arc: chỉ entry có source_chapter thuộc các chương trong scope
                            if sc is None or sc not in chapter_numbers:
                                continue
                        else:
                            # Toàn dự án: lấy hết (kể cả source_chapter null)
                            pass
                        bible_lines.append(f"  • {name}: {desc}")
            if "rule" in want:
                try:
                    # global: chỉ rule đã approve, type Info hoặc Unknown
                    r = (
                        supabase.table("project_rules")
                        .select("content, scope")
                        .eq("scope", "global")
                        .is_("story_id", "null")
                        .eq("approve", True)
                        .in_("type", list(RULE_TYPES_FOR_LOGIC))
                        .order("created_at", desc=True)
                        .limit(100)
                        .execute()
                    )
                    for row in (r.data or []):
                        c = (row.get("content") or "").strip()
                        if len(c) > 800:
                            c = c[:797] + "..."
                        if c:
                            rule_lines.append(f"  • [RULE] (global): {c}")
                    # project: chỉ rule đã approve, type Info hoặc Unknown
                    if project_id:
                        r = (
                            supabase.table("project_rules")
                            .select("content, scope")
                            .eq("scope", "project")
                            .eq("story_id", project_id)
                            .eq("approve", True)
                            .in_("type", list(RULE_TYPES_FOR_LOGIC))
                            .order("created_at", desc=True)
                            .limit(100)
                            .execute()
                        )
                        for row in (r.data or []):
                            c = (row.get("content") or "").strip()
                            if len(c) > 800:
                                c = c[:797] + "..."
                            if c:
                                rule_lines.append(f"  • [RULE] (project): {c}")
                    # arc: rule thuộc arc hiện tại + các arc sequential, chỉ type Info & Unknown
                    if project_id and arc_ids:
                        r = (
                            supabase.table("project_rules")
                            .select("content, scope")
                            .eq("scope", "arc")
                            .eq("story_id", project_id)
                            .in_("arc_id", list(arc_ids))
                            .eq("approve", True)
                            .in_("type", list(RULE_TYPES_FOR_LOGIC))
                            .order("created_at", desc=True)
                            .limit(100)
                            .execute()
                        )
                        for row in (r.data or []):
                            c = (row.get("content") or "").strip()
                            if len(c) > 800:
                                c = c[:797] + "..."
                            if c:
                                rule_lines.append(f"  • [RULE] (arc): {c}")
                        # Rule gán nhiều arc qua project_rule_arcs
                        try:
                            pra = supabase.table("project_rule_arcs").select("rule_id").in_("arc_id", list(arc_ids)).execute()
                            rule_ids = list({row["rule_id"] for row in (pra.data or []) if row.get("rule_id")})
                            if rule_ids:
                                r2 = (
                                    supabase.table("project_rules")
                                    .select("content, scope")
                                    .eq("story_id", project_id)
                                    .in_("id", rule_ids[:100])
                                    .eq("approve", True)
                                    .in_("type", list(RULE_TYPES_FOR_LOGIC))
                                    .order("created_at", desc=True)
                                    .limit(100)
                                    .execute()
                                )
                                for row in (r2.data or []):
                                    c = (row.get("content") or "").strip()
                                    if len(c) > 800:
                                        c = c[:797] + "..."
                                    if c:
                                        rule_lines.append(f"  • [RULE] (arc): {c}")
                        except Exception:
                            pass
                except Exception:
                    pass
            if "chat_crystallize" in want:
                try:
                    # scope=project (toàn dự án) + scope=arc với arc_id trong arc_ids của scope hiện tại
                    seen_ids = set()
                    q_project = supabase.table("chat_crystallize_entries").select("id, title, description").eq("scope", "project").eq("story_id", project_id)
                    r_project = q_project.order("created_at", desc=True).limit(50).execute()
                    for row in (r_project.data or []):
                        rid = row.get("id")
                        if rid and rid not in seen_ids:
                            seen_ids.add(rid)
                            title = (row.get("title") or "").strip()
                            desc = (row.get("description") or "").strip()
                            if len(desc) > 800:
                                desc = desc[:797] + "..."
                            if title or desc:
                                chat_lines.append(f"  • {title}: {desc}")
                    if arc_ids:
                        q_arc = supabase.table("chat_crystallize_entries").select("id, title, description").eq("scope", "arc").eq("story_id", project_id).in_("arc_id", list(arc_ids)[:50])
                        r_arc = q_arc.order("created_at", desc=True).limit(50).execute()
                        for row in (r_arc.data or []):
                            rid = row.get("id")
                            if rid and rid not in seen_ids:
                                seen_ids.add(rid)
                                title = (row.get("title") or "").strip()
                                desc = (row.get("description") or "").strip()
                                if len(desc) > 800:
                                    desc = desc[:797] + "..."
                                if title or desc:
                                    chat_lines.append(f"  • {title}: {desc}")
                except Exception:
                    pass

            if bible_lines:
                parts.append("[BIBLE - Nhân vật / khái niệm]\n" + "\n".join(bible_lines))
            if rule_lines:
                parts.append("[RULE - Quy tắc đã lưu]\n" + "\n".join(rule_lines))
            if chat_lines:
                parts.append("[CHAT CRYSTALLIZE - Điểm nhớ từ hội thoại]\n" + "\n".join(chat_lines))

        # 5) Relations: chỉ quan hệ có source_chapter thuộc scope (chương mục tiêu + sequential arc)
        if "relation" in want:
            bible_entries = _get_bible_for_logic(project_id, include_archived=include_archived)
            id_to_name = {str(e.get("id")): (e.get("entity_name") or "").strip() for e in bible_entries if e.get("id")}
            try:
                q = supabase.table("entity_relations").select("*").eq("story_id", project_id)
                if arc_ids and chapter_numbers:
                    # Scope theo arc: chỉ relation có source_chapter thuộc các chương trong scope
                    q = q.in_("source_chapter", list(chapter_numbers))
                rel_res = q.execute()
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
                else:
                    parts.append("[QUAN HỆ] Chưa có dữ liệu.")
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
    dimensions: Optional[List[str]] = None,
) -> Tuple[List[Dict[str, Any]], int, Optional[int], str]:
    """
    Chạy soát logic 1 chương. Tạo chapter_logic_checks, gọi LLM, ghi chapter_logic_issues.
    dimensions: None hoặc rỗng = soát cả 5 dimension; nếu có thì chỉ soát các dimension trong list (timeline, bible, relation, chat_crystallize, rule).
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
            project_id, chapter_id, chapter_number, arc_id, include_archived=False, dimensions=dimensions
        )
        content_slice = (chapter_content or "")[:max_content_chars]
        if len(chapter_content or "") > max_content_chars:
            content_slice += "\n\n[... (nội dung cắt bớt do giới hạn)]"

        dim_list = [d for d in (dimensions or []) if (d or "").strip() and (d or "").strip().lower() in LOGIC_DIMENSIONS]
        if not dim_list:
            dim_list = list(LOGIC_DIMENSIONS)
        dim_instruction = ", ".join(dim_list)
        scope_note = f" Chỉ soát các dimension: {dim_instruction}." if dimensions and len(dimensions) > 0 else ""

        prompt = f"""Bạn là trợ lý kiểm tra tính logic của truyện. Soát nội dung chương dưới đây với nguồn tham chiếu đã cung cấp.{scope_note}

DỮ LIỆU THAM CHIẾU (đã thiết lập trong dự án):
---
{context_ref}
---

NỘI DUNG CHƯƠNG CẦN SOÁT (Chương #{chapter_number}: {chapter_title}):
---
{content_slice}
---

YÊU CẦU: Tìm mâu thuẫn logic, điểm vô lý, plot hole theo từng nguồn có trong DỮ LIỆU THAM CHIẾU: Timeline (sự kiện trái thứ tự/mô tả?), Bible (nhân vật/địa điểm sai lệch?), Relation (quan hệ đúng entity_relations?), Chat crystallize (trái [CHAT]?), Rule (vi phạm [RULE]?). Chỉ báo lỗi thuộc dimension đã cho.

Trả về ĐÚNG MỘT mảng JSON, mỗi phần tử: "dimension" (một trong: timeline, bible, relation, chat_crystallize, rule), "message" (mô tả ngắn lỗi), "details" (object tùy chọn). Nếu không có lỗi, trả về mảng rỗng [].
Ví dụ: [{{"dimension": "bible", "message": "Nhân vật A trong chương được mô tả khác với Bible.", "details": {{}}}}]
Chỉ trả về JSON, không giải thích thêm."""

        try:
            response = AIService.call_openrouter(
                messages=[{"role": "user", "content": prompt}],
                model=_get_default_tool_model(),
                temperature=0.2,
                max_tokens=LOGIC_CHECK_MAX_TOKENS,
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
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """Lấy danh sách chapter_logic_issues. chapter_id=None: tất cả chương. status_filter: active | resolved | None (cả hai). offset/limit để phân trang ở DB."""
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
        q = q.order("created_at", desc=True)
        if offset > 0 or limit != 200:
            q = q.range(offset, offset + max(0, limit - 1))
        else:
            q = q.limit(limit)
        r = q.execute()
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
