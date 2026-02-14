"""
Parser chỉ lệnh @@ — map alias/trigger -> command_key, parse tham số, trả về router_out hoặc fallback.
Kích hoạt bằng @@ (tránh nhầm với email @). Fallback: thiếu thông tin hoặc không nhận diện -> ask_user_clarification.
"""
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# Built-in defaults khi chưa có bảng command_definitions (sau migration sẽ dùng DB)
BUILTIN_TRIGGERS = {
    "extract_bible": "extract_bible",
    "extract_relation": "extract_relation",
    "extract_timeline": "extract_timeline",
    "extract_chunking": "extract_chunking",
    "delete_bible": "delete_bible",
    "delete_relation": "delete_relation",
    "delete_timeline": "delete_timeline",
    "delete_chunking": "delete_chunking",
    "data_analyze": "data_analyze",
    "summarize": "summarize_chapter",
    "summarize_chapter": "summarize_chapter",
    "read": "read_chapter",
    "read_chapter": "read_chapter",
    "search_bible": "search_bible",
    "search_chunks": "search_chunks",
    "timeline": "manage_timeline",
    "manage_timeline": "manage_timeline",
    "mixed": "mixed_context",
    "mixed_context": "mixed_context",
    "calc": "numerical_calculation",
    "numerical_calculation": "numerical_calculation",
    "web": "web_search",
    "web_search": "web_search",
    "remember": "remember_rule",
    "remember_rule": "remember_rule",
    "sql": "query_sql",
    "query_sql": "query_sql",
    "chapters": "list_chapters",
    "list_chapters": "list_chapters",
    "v7": "suggest_v7",
    "suggest_v7": "suggest_v7",
}

# command_key -> (intent, data_operation_type, data_operation_target) cho update_data
COMMAND_TO_ROUTER = {
    "extract_bible": ("update_data", "extract", "bible"),
    "extract_relation": ("update_data", "extract", "relation"),
    "extract_timeline": ("update_data", "extract", "timeline"),
    "extract_chunking": ("update_data", "extract", "chunking"),
    "delete_bible": ("update_data", "delete", "bible"),
    "delete_relation": ("update_data", "delete", "relation"),
    "delete_timeline": ("update_data", "delete", "timeline"),
    "delete_chunking": ("update_data", "delete", "chunking"),
    "data_analyze": ("update_data", None, None),  # đặc biệt: 4 bước
    "summarize_chapter": ("read_full_content", None, None),
    "read_chapter": ("read_full_content", None, None),
    "search_bible": ("search_bible", None, None),
    "search_chunks": ("search_chunks", None, None),
    "manage_timeline": ("manage_timeline", None, None),
    "mixed_context": ("mixed_context", None, None),
    "numerical_calculation": ("numerical_calculation", None, None),
    "web_search": ("web_search", None, None),
    "remember_rule": ("update_data", "remember_rule", "rule"),
    "query_sql": ("query_Sql", None, None),
    "list_chapters": ("read_full_content", None, None),
    "suggest_v7": ("suggest_v7", None, None),
    "ask_user_clarification": ("ask_user_clarification", None, None),
}


@dataclass
class ParsedCommand:
    """Kết quả parse thành công: đủ thông tin để build router_out."""
    command_key: str
    intent: str
    router_out: Dict[str, Any]
    raw_trigger: str


@dataclass
class ParseResult:
    """Kết quả parse: ok | incomplete | unknown."""
    status: str  # "ok" | "incomplete" | "unknown"
    parsed: Optional[ParsedCommand] = None
    clarification_message: str = ""


def _parse_chapter_range_arg(s: str) -> Optional[Tuple[int, int]]:
    """Parse '5', '1-10', '1 - 5' -> (start, end)."""
    if not s or not isinstance(s, str):
        return None
    s = s.strip()
    # Khoảng: 1-10, 1 - 10
    m = re.match(r"^(\d+)\s*-\s*(\d+)$", s)
    if m:
        try:
            a, b = int(m.group(1)), int(m.group(2))
            if a >= 1 and b >= 1:
                return (min(a, b), max(a, b))
        except ValueError:
            pass
    # Một số
    m = re.match(r"^(\d+)$", s)
    if m:
        try:
            n = int(m.group(1))
            if n >= 1:
                return (n, n)
        except ValueError:
            pass
    return None


def _get_definitions_and_aliases(story_id: Optional[str], user_id: Optional[str]):
    """Lấy command_definitions + command_aliases từ DB. Trả về (defs_by_key, alias_to_command_key)."""
    defs_by_key = {}
    alias_to_key = {}
    try:
        from config import init_services
        svc = init_services()
        if not svc:
            return _builtin_defs(), BUILTIN_TRIGGERS
        sb = svc["supabase"]
        # Definitions
        r = sb.table("command_definitions").select("*").order("sort_order").execute()
        if r.data:
            for row in r.data:
                key = row.get("command_key")
                if key:
                    defs_by_key[key] = row
                    alias_to_key[(row.get("default_trigger") or "").strip().lower()] = key
        # Aliases (override) cho story
        if story_id:
            r2 = sb.table("command_aliases").select("alias, command_key").eq("story_id", story_id).execute()
            if r2.data:
                for row in r2.data:
                    a = (row.get("alias") or "").strip().lower()
                    if a:
                        alias_to_key[a] = row.get("command_key")
    except Exception:
        pass
    if not defs_by_key:
        defs_by_key = _builtin_defs()
        alias_to_key = {k.lower(): v for k, v in BUILTIN_TRIGGERS.items()}
    return defs_by_key, alias_to_key


def _builtin_defs() -> Dict[str, Dict]:
    """Định nghĩa mặc định (args_schema) khi không có DB."""
    return {
        "extract_bible": {"args_schema": [{"name": "chapter_range", "required": True, "type": "chapter_range"}]},
        "extract_relation": {"args_schema": [{"name": "chapter_range", "required": True, "type": "chapter_range"}]},
        "extract_timeline": {"args_schema": [{"name": "chapter_range", "required": True, "type": "chapter_range"}]},
        "extract_chunking": {"args_schema": [{"name": "chapter_range", "required": True, "type": "chapter_range"}]},
        "delete_bible": {"args_schema": [{"name": "chapter_range", "required": True, "type": "chapter_range"}]},
        "delete_relation": {"args_schema": [{"name": "chapter_range", "required": True, "type": "chapter_range"}]},
        "delete_timeline": {"args_schema": [{"name": "chapter_range", "required": True, "type": "chapter_range"}]},
        "delete_chunking": {"args_schema": [{"name": "chapter_range", "required": True, "type": "chapter_range"}]},
        "data_analyze": {"args_schema": [{"name": "chapter_range", "required": True, "type": "chapter_range"}]},
        "summarize_chapter": {"args_schema": [{"name": "chapter_range", "required": True, "type": "chapter_range"}]},
        "read_chapter": {"args_schema": [{"name": "chapter_range", "required": True, "type": "chapter_range"}]},
        "search_bible": {"args_schema": [{"name": "query", "required": True, "type": "string"}]},
        "search_chunks": {"args_schema": [{"name": "query", "required": True, "type": "string"}]},
        "manage_timeline": {"args_schema": [{"name": "query", "required": True, "type": "string"}]},
        "mixed_context": {"args_schema": [{"name": "chapter_range", "required": True}, {"name": "query", "required": True}]},
        "numerical_calculation": {"args_schema": [{"name": "query", "required": True, "type": "string"}]},
        "web_search": {"args_schema": [{"name": "query", "required": True, "type": "string"}]},
        "remember_rule": {"args_schema": [{"name": "summary", "required": True, "type": "string"}]},
        "query_sql": {"args_schema": [{"name": "query", "required": True, "type": "string"}]},
        "list_chapters": {"args_schema": []},
        "suggest_v7": {"args_schema": []},
        "ask_user_clarification": {"args_schema": []},
    }


def _build_router_out(
    command_key: str,
    intent: str,
    chapter_range: Optional[Tuple[int, int]] = None,
    query_text: str = "",
    update_summary: str = "",
) -> Dict[str, Any]:
    """Tạo dict giống output của SmartAIRouter cho executor."""
    out = {
        "intent": intent,
        "target_files": [],
        "target_bible_entities": [],
        "rewritten_query": query_text or "",
        "chapter_range": list(chapter_range) if chapter_range else None,
        "chapter_range_mode": "range" if chapter_range else None,
        "chapter_range_count": 5,
        "clarification_question": "",
        "update_summary": update_summary,
    }
    if intent == "update_data":
        t = COMMAND_TO_ROUTER.get(command_key, (None, None, None))
        out["data_operation_type"] = t[1] or "extract"
        out["data_operation_target"] = t[2] or "bible"
        if command_key == "remember_rule":
            out["update_summary"] = update_summary or query_text
        if command_key == "data_analyze":
            out["data_operation_type"] = "extract"
            out["data_operation_target"] = "bible"
            out["_data_analyze_full"] = True
    if command_key == "list_chapters":
        out["rewritten_query"] = "Liệt kê danh sách chương"
    return out


def parse_command(
    message: str,
    story_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> ParseResult:
    """
    Parse tin nhắn có dạng @trigger [args...].
    - Nếu không có @ hoặc không phải trigger nào -> status='unknown', clarification_message gợi ý.
    - Nếu có trigger nhưng thiếu tham số bắt buộc -> status='incomplete', clarification_message hỏi lại.
    - Nếu đủ -> status='ok', parsed=ParsedCommand(router_out).
    """
    if not message or not isinstance(message, str):
        return ParseResult("unknown", clarification_message="Tin nhắn trống. Bạn muốn thực hiện thao tác gì? (Xem tab **Chỉ lệnh** để biết cú pháp.)")
    text = message.strip()
    if "@@" not in text:
        return ParseResult("unknown", clarification_message="")
    # Tìm @@ và token ngay sau nó (tránh nhầm với email @)
    idx = text.index("@@")
    after_at = text[idx + 2:].strip()
    if not after_at:
        return ParseResult(
            "incomplete",
            clarification_message="Bạn gõ @@ nhưng chưa chỉ rõ lệnh. Ví dụ: @@extract_bible 1-3, @@search_bible nhân vật A. Xem tab **Chỉ lệnh** để biết danh sách lệnh và cú pháp."
        )
    parts = after_at.split(maxsplit=1)
    trigger_raw = parts[0].strip().lower()
    rest = (parts[1].strip() if len(parts) > 1 else "").strip()
    if not trigger_raw:
        return ParseResult("incomplete", clarification_message="Sau @@ cần tên lệnh. Ví dụ: @@extract_bible 1")

    defs_by_key, alias_to_key = _get_definitions_and_aliases(story_id, user_id)
    command_key = alias_to_key.get(trigger_raw)
    if not command_key:
        return ParseResult(
            "unknown",
            clarification_message=f"Không nhận diện được lệnh **@@{trigger_raw}**. Bạn có thể xem tab **Chỉ lệnh** để biết các lệnh hợp lệ và cú pháp. Bạn muốn thực hiện thao tác gì?"
        )
    cmd_def = defs_by_key.get(command_key) or _builtin_defs().get(command_key)
    args_schema = (cmd_def.get("args_schema") if isinstance(cmd_def, dict) else []) or []
    if not isinstance(args_schema, list):
        args_schema = []

    intent, op_type, op_target = COMMAND_TO_ROUTER.get(command_key, ("chat_casual", None, None))
    chapter_range = None
    query_text = rest
    update_summary = ""

    for arg_def in args_schema:
        name = (arg_def if isinstance(arg_def, dict) else {}).get("name") or ""
        required = (arg_def if isinstance(arg_def, dict) else {}).get("required", True)
        typ = (arg_def if isinstance(arg_def, dict) else {}).get("type") or "string"
        if name == "chapter_range":
            chapter_range = _parse_chapter_range_arg(rest)
            if required and not chapter_range and rest:
                chapter_range = _parse_chapter_range_arg(rest.split()[0] if rest else "")
            if required and not chapter_range:
                return ParseResult(
                    "incomplete",
                    clarification_message=f"Lệnh **@@{trigger_raw}** cần chỉ rõ **chương** (ví dụ: @@{trigger_raw} 5 hoặc @@{trigger_raw} 1-10). Bạn muốn áp dụng cho chương nào?"
                )
            if chapter_range:
                query_text = ""
            break
        if name in ("query", "summary"):
            if required and not rest.strip():
                return ParseResult(
                    "incomplete",
                    clarification_message=f"Lệnh **@@{trigger_raw}** cần thêm nội dung (ví dụ: @@{trigger_raw} nội dung cần tìm hoặc ghi nhớ). Bạn muốn nhập gì?"
                )
            if name == "summary":
                update_summary = rest
            break

    if command_key == "mixed_context":
        # mixed cần cả chapter_range và query: "3 nhân vật A làm gì"
        first_word = (rest.split() or [""])[0]
        chapter_range = _parse_chapter_range_arg(first_word)
        if chapter_range:
            query_text = " ".join(rest.split()[1:]).strip()
        if not chapter_range or not query_text:
            return ParseResult(
                "incomplete",
                clarification_message="Lệnh **@@mixed** cần số chương và câu hỏi (ví dụ: @@mixed 3 nhân vật A làm gì và quan hệ với B). Bạn muốn hỏi chương nào và nội dung gì?"
            )

    if command_key == "data_analyze" and not chapter_range:
        return ParseResult(
            "incomplete",
            clarification_message="Lệnh **@@data_analyze** cần chỉ rõ chương hoặc khoảng chương (ví dụ: @@data_analyze 1-5). Bạn muốn chạy cho chương nào?"
        )

    router_out = _build_router_out(command_key, intent, chapter_range=chapter_range, query_text=query_text, update_summary=update_summary)
    if command_key == "data_analyze":
        router_out["_data_analyze_full"] = True
    return ParseResult("ok", parsed=ParsedCommand(command_key=command_key, intent=intent, router_out=router_out, raw_trigger=trigger_raw))


def is_command_message(message: str) -> bool:
    """True nếu tin nhắn có chứa @@ (kích hoạt chỉ lệnh; tránh nhầm email @)."""
    return isinstance(message, str) and "@@" in message.strip()


def get_fallback_clarification(parse_result: ParseResult) -> str:
    """Câu hỏi làm rõ thống nhất khi fallback ask_user_clarification."""
    if parse_result.status == "ok":
        return ""
    return (
        parse_result.clarification_message
        or "Chỉ lệnh không đủ thông tin hoặc không nhận diện được. Bạn muốn thực hiện thao tác gì? (Xem tab **Chỉ lệnh** để biết cú pháp.)"
    )
