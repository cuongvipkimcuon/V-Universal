# ai/context_schema.py - Schema và chuẩn hóa cho search_context (context_needs, context_priority)
"""Định nghĩa hợp lệ cho context_needs và hàm chuẩn hóa/suy mặc định."""
from typing import Any, Dict, List

VALID_CONTEXT_NEEDS = frozenset({"bible", "relation", "timeline", "chunk", "chapter"})


def normalize_context_needs(needs: Any) -> List[str]:
    """Chuẩn hóa context_needs: lowercase, chỉ giữ phần tử thuộc VALID_CONTEXT_NEEDS, giữ thứ tự."""
    if not needs:
        return []
    if not isinstance(needs, list):
        return []
    out = []
    seen = set()
    for x in needs:
        s = str(x).strip().lower() if x else ""
        if s and s in VALID_CONTEXT_NEEDS and s not in seen:
            out.append(s)
            seen.add(s)
    return out


def infer_default_context_needs(router_result: Dict) -> List[str]:
    """Suy context_needs mặc định khi intent search_context nhưng context_needs rỗng."""
    intent = (router_result.get("intent") or "").strip().lower()
    if intent != "search_context":
        return []
    entities = router_result.get("target_bible_entities") or []
    ch_range = router_result.get("chapter_range")
    query = (router_result.get("rewritten_query") or "").lower()
    needs = []
    if ch_range or "chương" in query or "chapter" in query or "tóm tắt" in query:
        needs.append("chapter")
    if entities or "quan hệ" in query or "relation" in query or "nhân vật" in query or "lore" in query:
        needs.extend(["bible", "relation"])
    if "timeline" in query or "sự kiện" in query or "mốc thời gian" in query or "thứ tự" in query:
        needs.append("timeline")
    if "ai nói" in query or "câu nào" in query or "chi tiết" in query or "vũ khí" in query:
        needs.append("chunk")
    # Dedupe, order: chapter, bible, relation, timeline, chunk
    order = ["chapter", "bible", "relation", "timeline", "chunk"]
    seen = set()
    result = []
    for k in order:
        if k in needs and k not in seen:
            result.append(k)
            seen.add(k)
    for k in needs:
        if k not in seen and k in VALID_CONTEXT_NEEDS:
            result.append(k)
            seen.add(k)
    return result if result else ["bible", "relation"]


def normalize_context_priority(priority: Any, context_needs: List[str]) -> List[str]:
    """Chuẩn hóa context_priority: chỉ gồm phần tử trong context_needs, theo thứ tự ưu tiên (ưu tiên trước)."""
    if not priority or not isinstance(priority, list):
        return list(context_needs) if context_needs else []
    need_set = set(context_needs)
    out = []
    for x in priority:
        s = str(x).strip().lower() if x else ""
        if s in need_set and s not in out:
            out.append(s)
    for n in context_needs:
        if n not in out:
            out.append(n)
    return out
