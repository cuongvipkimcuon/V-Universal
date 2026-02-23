# core/observability.py - V8.3 Log chat turn (intent, context_tokens, llm_calls) for observability
"""Ghi log mỗi turn chat vào chat_turn_logs (nếu bảng tồn tại)."""
from typing import Any, Dict, List, Optional


def log_chat_turn(
    story_id: Optional[str],
    user_id: Optional[str],
    intent: str,
    context_needs: Optional[List[str]] = None,
    context_tokens: Optional[int] = None,
    llm_calls_count: Optional[int] = None,
    verification_used: bool = False,
) -> None:
    """Ghi một dòng vào chat_turn_logs. Bỏ qua khi không có supabase hoặc bảng chưa có."""
    try:
        from config import init_services
        services = init_services()
        if not services or not services.get("supabase"):
            return
        supabase = services["supabase"]
        row = {
            "story_id": story_id,
            "user_id": str(user_id) if user_id else None,
            "intent": intent or "chat_casual",
            "context_needs": context_needs if isinstance(context_needs, list) else None,
            "context_tokens": context_tokens,
            "llm_calls_count": llm_calls_count,
            "verification_used": verification_used,
        }
        supabase.table("chat_turn_logs").insert(row).execute()
    except Exception as e:
        print(f"log_chat_turn error: {e}")
