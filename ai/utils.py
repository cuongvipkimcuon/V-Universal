# ai/utils.py - Hàm tiện ích: caps, chapter, bible, format, rerank
import re
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

from config import Config, init_services

from ai.service import AIService


ROUTER_PLANNER_CHAT_HISTORY_MAX_TOKENS = 6000

VECTOR_WEIGHT = 0.7
RECENCY_WEIGHT = 0.1
IMPORTANCE_WEIGHT = 0.2
RECENCY_BONUS_HOURS = 24

PREFIX_WEIGHT = 0.15
VECTOR_WEIGHT_WITH_PREFIX = 0.55
RECENCY_WEIGHT_UNCHANGED = 0.1
IMPORTANCE_WEIGHT_UNCHANGED = 0.2


def cap_context_to_tokens(text: str, max_tokens: int) -> Tuple[str, int]:
    """Kiểm tra và cắt context sao cho không vượt quá max_tokens."""
    if not text or max_tokens <= 0:
        return text or "", AIService.estimate_tokens(text or "")
    est = AIService.estimate_tokens(text)
    if est <= max_tokens:
        return text, est
    target_chars = max_tokens * 4
    out = text[:target_chars] if len(text) > target_chars else text
    est = AIService.estimate_tokens(out)
    while est > max_tokens and len(out) > 500:
        out = out[:-500]
        est = AIService.estimate_tokens(out)
    return out, est


def cap_chat_history_to_tokens(text: str, max_tokens: int = ROUTER_PLANNER_CHAT_HISTORY_MAX_TOKENS) -> str:
    """Cắt lịch sử chat sao cho không vượt max_tokens; giữ phần đuôi."""
    if not text or max_tokens <= 0:
        return text or ""
    est = AIService.estimate_tokens(text)
    if est <= max_tokens:
        return text
    target_chars = max_tokens * 4
    if len(text) <= target_chars:
        return text
    out = text[-target_chars:]
    while AIService.estimate_tokens(out) > max_tokens and len(out) > 500:
        out = out[500:]
    return out


def _safe_float(value: Any, default: float = 0.5) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _recency_bonus(last_lookup_at: Any) -> float:
    if last_lookup_at is None:
        return 0.0
    try:
        if isinstance(last_lookup_at, str):
            dt = datetime.fromisoformat(last_lookup_at.replace("Z", "+00:00"))
        else:
            dt = last_lookup_at
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        delta = now - dt
        return 1.0 if delta <= timedelta(hours=RECENCY_BONUS_HOURS) else 0.0
    except Exception:
        return 0.0


def _rerank_by_score(rows: List[Dict], top_k: int) -> List[Dict]:
    for item in rows:
        vector_sim = _safe_float(item.get("similarity") or item.get("score"), 0.5)
        vector_sim = max(0.0, min(1.0, vector_sim))
        recency = _recency_bonus(item.get("last_lookup_at"))
        importance = _safe_float(item.get("importance_bias"), 0.5)
        importance = max(0.0, min(1.0, importance))
        item["_final_score"] = (vector_sim * VECTOR_WEIGHT) + (recency * RECENCY_WEIGHT) + (importance * IMPORTANCE_WEIGHT)
    sorted_rows = sorted(rows, key=lambda x: x.get("_final_score", 0.0), reverse=True)
    for item in sorted_rows:
        item.pop("_final_score", None)
    return sorted_rows[:top_k]


def _rerank_by_score_with_breakdown(rows: List[Dict], top_k: int) -> List[Dict]:
    for item in rows:
        vector_sim = _safe_float(item.get("similarity") or item.get("score"), 0.5)
        vector_sim = max(0.0, min(1.0, vector_sim))
        recency = _recency_bonus(item.get("last_lookup_at"))
        importance = _safe_float(item.get("importance_bias"), 0.5)
        importance = max(0.0, min(1.0, importance))
        item["score_vector"] = round(vector_sim * VECTOR_WEIGHT, 4)
        item["score_recency"] = round(recency * RECENCY_WEIGHT, 4)
        item["score_bias"] = round(importance * IMPORTANCE_WEIGHT, 4)
        item["score_final"] = round(item["score_vector"] + item["score_recency"] + item["score_bias"], 4)
    sorted_rows = sorted(rows, key=lambda x: x.get("score_final", 0.0), reverse=True)
    return sorted_rows[:top_k]


def extract_prefix(name: str) -> Tuple[str, str]:
    if not name or not isinstance(name, str):
        return "", (name or "")
    s = name.strip()
    if not s:
        return "", name
    try:
        if s.startswith("["):
            idx = s.find("]")
            if idx > 0:
                prefix = s[1:idx].strip()
                rest = s[idx + 1:].strip()
                return prefix, rest if rest else s
        return "", s
    except Exception:
        return "", s


def _estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)


def get_prefix_key_from_entity_name(entity_name: str) -> str:
    if not entity_name or not isinstance(entity_name, str):
        return "OTHER"
    prefix, _ = extract_prefix(entity_name.strip())
    return (prefix or "OTHER").strip().upper().replace(" ", "_") or "OTHER"


def _rerank_by_score_with_prefix(
    rows: List[Dict],
    top_k: int,
    inferred_prefixes: Optional[List[str]] = None,
) -> List[Dict]:
    if not inferred_prefixes:
        return _rerank_by_score(rows, top_k)
    normalized_inferred = {str(p).strip().upper().replace(" ", "_") for p in inferred_prefixes if p}
    for item in rows:
        vector_sim = _safe_float(item.get("similarity") or item.get("score"), 0.5)
        vector_sim = max(0.0, min(1.0, vector_sim))
        recency = _recency_bonus(item.get("last_lookup_at"))
        importance = _safe_float(item.get("importance_bias"), 0.5)
        importance = max(0.0, min(1.0, importance))
        pk = get_prefix_key_from_entity_name(item.get("entity_name") or "")
        prefix_bonus = 1.0 if pk in normalized_inferred else 0.0
        item["_final_score"] = (
            (vector_sim * VECTOR_WEIGHT_WITH_PREFIX)
            + (recency * RECENCY_WEIGHT_UNCHANGED)
            + (importance * IMPORTANCE_WEIGHT_UNCHANGED)
            + (prefix_bonus * PREFIX_WEIGHT)
        )
    sorted_rows = sorted(rows, key=lambda x: x.get("_final_score", 0.0), reverse=True)
    for item in sorted_rows:
        item.pop("_final_score", None)
    return sorted_rows[:top_k]


def _get_prefix_section_order_and_labels() -> Tuple[List[str], Dict[str, str]]:
    setup = Config.get_prefix_setup()
    order = []
    labels: Dict[str, str] = {}
    for p in setup:
        pk = (p.get("prefix_key") or "").strip().upper().replace(" ", "_")
        if pk:
            order.append(pk)
            labels[pk] = pk
    return order, labels


def _filter_bible_by_chapter_range(
    raw_list: List[Dict],
    chapter_range: Optional[Tuple[int, int]],
    max_items: int = 15,
) -> List[Dict]:
    if not raw_list or not chapter_range or len(chapter_range) < 2:
        return raw_list
    start, end = int(chapter_range[0]), int(chapter_range[1])
    start, end = min(start, end), max(start, end)
    filtered = [r for r in raw_list if r.get("source_chapter") is not None and start <= int(r.get("source_chapter", 0)) <= end]
    if not filtered:
        return raw_list
    return filtered[:max_items]


def format_bible_context_by_sections(raw_list: List[Dict]) -> str:
    if not raw_list:
        return ""
    grouped: Dict[str, List[Dict]] = defaultdict(list)
    for item in raw_list:
        pk = get_prefix_key_from_entity_name(item.get("entity_name") or "")
        grouped[pk].append(item)
    order, labels = _get_prefix_section_order_and_labels()
    seen = set(order)
    for pk in grouped:
        if pk not in seen:
            order.append(pk)
            if pk not in labels:
                labels[pk] = pk
    sections = []
    for pk in order:
        items = grouped.get(pk, [])
        if not items:
            continue
        label = labels.get(pk, pk)
        block = "\n".join(
            f"- [{e.get('entity_name', '')}]: {e.get('description', '')}"
            for e in items
        )
        sections.append(f"\n--- {label} ---\n{block}")
    return "\n".join(sections).strip()


def get_chapter_list_for_router(project_id: str) -> str:
    if not project_id:
        return "(Trống)"
    try:
        services = init_services()
        if not services:
            return "(Trống)"
        r = (
            services["supabase"]
            .table("chapters")
            .select("chapter_number, title")
            .eq("story_id", project_id)
            .order("chapter_number")
            .execute()
        )
        rows = list(r.data) if r.data else []
        if not rows:
            return "(Trống)"
        parts = []
        for row in rows:
            num = row.get("chapter_number") or 0
            title = (row.get("title") or "").strip() or f"Chương {num}"
            parts.append(f"{num} - {title}")
        return ", ".join(parts)
    except Exception:
        return "(Trống)"


def parse_chapter_range_from_query(query: str) -> Optional[Tuple[int, int]]:
    if not query or not isinstance(query, str) or not query.strip():
        return None
    q = query.strip().lower()
    range_match = re.search(
        r"(?:chương|chapter)\s*(\d+)\s*(?:đến|tới|to|-)\s*(?:chương|chapter)?\s*(\d+)",
        q, re.IGNORECASE,
    )
    if range_match:
        try:
            a, b = int(range_match.group(1)), int(range_match.group(2))
            return (min(a, b), max(a, b))
        except (ValueError, IndexError):
            pass
    single_match = re.search(r"(?:chương|chapter)\s*(\d+)", q, re.IGNORECASE)
    if single_match:
        try:
            n = int(single_match.group(1))
            if n >= 1:
                return (n, n)
        except (ValueError, IndexError):
            pass
    return None


def get_bible_index(story_id: str, max_tokens: int = 2000) -> str:
    if not story_id:
        return ""
    try:
        services = init_services()
        if not services:
            return ""
        supabase = services["supabase"]
        try:
            rows = (
                supabase.table("story_bible")
                .select("entity_name, lookup_count, importance_bias, parent_id")
                .eq("story_id", story_id)
                .execute()
            )
        except Exception:
            try:
                rows = (
                    supabase.table("story_bible")
                    .select("entity_name, lookup_count, importance_bias")
                    .eq("story_id", story_id)
                    .execute()
                )
            except Exception:
                return ""
        data = list(rows.data) if rows.data else []
        for r in data:
            r.setdefault("parent_id", None)
        def _score(r):
            try:
                lk = int(r.get("lookup_count") or 0)
                bi = r.get("importance_bias")
                b = float(bi) if bi is not None else 0.0
                return lk + b
            except (TypeError, ValueError):
                return 0
        data.sort(key=_score, reverse=True)
        top100 = data[:100]
        parent_ids = [r["parent_id"] for r in top100 if r.get("parent_id")]
        parent_names: Dict[Any, str] = {}
        if parent_ids:
            try:
                ids = list(set(str(pid) for pid in parent_ids if pid is not None))
                if ids:
                    pr = supabase.table("story_bible").select("id, entity_name").in_("id", ids).execute()
                    if pr.data:
                        for row in pr.data:
                            try:
                                _, disp = extract_prefix(row.get("entity_name") or "")
                                parent_names[row.get("id")] = disp.strip() or "(gốc)"
                            except Exception:
                                parent_names[row.get("id")] = (row.get("entity_name") or "").strip() or "(gốc)"
            except Exception:
                pass
        lines = []
        for r in top100:
            name = r.get("entity_name")
            if not name:
                continue
            line = f"Entity: {name}"
            pid = r.get("parent_id")
            if pid is not None and parent_names.get(pid):
                line += f" (gốc: {parent_names[pid]})"
            lines.append(line)
        out = "\n".join(lines) if lines else ""
        if _estimate_tokens(out) > max_tokens:
            out = out[: max(100, max_tokens * 4)]
        return out
    except Exception as e:
        print(f"get_bible_index error: {e}")
        return ""


def get_bible_entries(story_id: str) -> List[Dict[str, Any]]:
    if not story_id:
        return []
    try:
        services = init_services()
        if not services:
            return []
        supabase = services["supabase"]
        r = (
            supabase.table("story_bible")
            .select("id, entity_name")
            .eq("story_id", story_id)
            .execute()
        )
        return list(r.data) if r.data else []
    except Exception:
        return []


def get_timeline_events(
    project_id: str,
    limit: int = 50,
    chapter_range: Optional[Tuple[int, int]] = None,
    arc_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    if not project_id:
        return []
    try:
        services = init_services()
        if not services:
            return []
        supabase = services["supabase"]
        q = (
            supabase.table("timeline_events")
            .select("id, event_order, title, description, raw_date, event_type, chapter_id, arc_id")
            .eq("story_id", project_id)
            .order("event_order")
        )
        if arc_id:
            q = q.eq("arc_id", arc_id)
        if chapter_range and len(chapter_range) >= 2:
            start, end = int(chapter_range[0]), int(chapter_range[1])
            start, end = min(start, end), max(start, end)
            ch_res = supabase.table("chapters").select("id").eq(
                "story_id", project_id
            ).gte("chapter_number", start).lte("chapter_number", end).execute()
            chapter_ids = [row["id"] for row in (ch_res.data or []) if row.get("id")]
            if chapter_ids:
                q = q.in_("chapter_id", chapter_ids)
        r = q.limit(limit).execute()
        return list(r.data) if r.data else []
    except Exception as e:
        print(f"get_timeline_events error: {e}")
        return []
