# ai/content.py - suggest_relations, suggest_import_category, generate_*, extract_*, get_file_sample, analyze_split, execute_split
import json
import re
from typing import Any, Dict, List, Optional

from config import Config

from ai.service import AIService, _get_default_tool_model
from ai.utils import get_bible_entries


def suggest_relations(content: str, story_id: str) -> List[Dict[str, Any]]:
    """
    AI quét nội dung (chương/đoạn) và so khớp với bible_index để đề xuất:
    - Quan hệ giữa hai thực thể: Source, Target, Relation_Type, Reason -> kind="relation".
    - Nhân vật tiến hóa (1-n): thực thể mới cùng gốc -> gợi ý parent_id, kind="parent".
    """
    if not content or not content.strip() or not story_id:
        return []
    entries = get_bible_entries(story_id)
    if not entries:
        return []
    name_to_id = {}
    for e in entries:
        name = (e.get("entity_name") or "").strip()
        if name:
            name_to_id[name] = e.get("id")
    index_text = "\n".join([f"- {e.get('entity_name', '')}" for e in entries[:150]])
    prompt = f"""Bạn là trợ lý phân tích văn bản. Cho NỘI DUNG và DANH SÁCH THỰC THỂ (Bible) của một truyện.

DANH SÁCH THỰC THỂ (chính xác từ Bible):
{index_text}

NỘI DUNG (đoạn/chương cần phân tích):
---
{content[:15000]}
---

Nhiệm vụ:
1) QUAN HỆ: Tìm các cặp thực thể có tương tác/liên quan trong nội dung. Với mỗi cặp, trả về source, target, relation_type, reason.
2) NHÂN VẬT TIẾN HÓA (1-n): Nếu có thực thể mới là "phiên bản khác" của thực thể đã có -> gợi ý parent: entity, parent, reason.

Trả về ĐÚNG một JSON object với hai key:
- "relations": [ {{ "source": "...", "target": "...", "relation_type": "...", "reason": "..." }} ]
- "parent_suggestions": [ {{ "entity": "...", "parent": "...", "reason": "..." }} ]

Chỉ dùng tên có trong DANH SÁCH THỰC THỂ. Nếu không có gì phù hợp, trả về "relations": [] và "parent_suggestions": [].
Chỉ trả về JSON, không giải thích thêm."""

    try:
        response = AIService.call_openrouter(
            messages=[{"role": "user", "content": prompt}],
            model=_get_default_tool_model(),
            temperature=0.2,
            max_tokens=2000,
        )
        text = (response.choices[0].message.content or "").strip()
        text = re.sub(r"^```\w*\n?", "", text).strip()
        text = re.sub(r"\n?```\s*$", "", text).strip()
        data = json.loads(text)
        relations_in = data.get("relations") or []
        parent_in = data.get("parent_suggestions") or []

        def resolve_name(name: str) -> Optional[Any]:
            n = (name or "").strip()
            if n in name_to_id:
                return name_to_id[n]
            for k, vid in name_to_id.items():
                if n in k or k in n:
                    return vid
            return None

        out = []
        for r in relations_in:
            src_id = resolve_name(r.get("source") or "")
            tgt_id = resolve_name(r.get("target") or "")
            if src_id and tgt_id and src_id != tgt_id:
                out.append({
                    "kind": "relation",
                    "source_entity_id": src_id,
                    "target_entity_id": tgt_id,
                    "relation_type": (r.get("relation_type") or "liên quan").strip(),
                    "description": (r.get("reason") or "").strip(),
                    "story_id": story_id,
                })
        for p in parent_in:
            child_id = resolve_name(p.get("entity") or "")
            parent_id = resolve_name(p.get("parent") or "")
            if child_id and parent_id and child_id != parent_id:
                out.append({
                    "kind": "parent",
                    "entity_id": child_id,
                    "parent_entity_id": parent_id,
                    "reason": (p.get("reason") or "").strip(),
                })
        return out
    except Exception as e:
        print(f"suggest_relations error: {e}")
        return []


def suggest_import_category(text: str) -> str:
    """Gợi ý prefix/category cho nội dung import (dùng LLM nhẹ)."""
    if not text or len(text.strip()) < 20:
        return "[OTHER]"
    try:
        model = _get_default_tool_model()
        prefixes = Config.get_prefixes()
        if not prefixes:
            return "[OTHER]"
        if "[OTHER]" not in prefixes:
            prefixes = list(prefixes) + ["[OTHER]"]
        prompt = f"""Phân loại nội dung sau vào ĐÚNG MỘT trong các loại (chỉ trả về chuỗi loại, không giải thích):
{', '.join(prefixes)}

NỘI DUNG (rút gọn):
{text[:1500]}

Trả về đúng một chuỗi, ví dụ: [CHARACTER] hoặc [RULE]."""
        resp = AIService.call_openrouter(
            messages=[{"role": "user", "content": prompt}],
            model=model,
            temperature=0.1,
            max_tokens=50,
        )
        raw = (resp.choices[0].message.content or "").strip()
        for p in prefixes:
            if p in raw or (p.strip("[]") and p.strip("[]").lower() in raw.lower()):
                return p
        return "[OTHER]"
    except Exception as e:
        print(f"suggest_import_category error: {e}")
        return "[OTHER]"


def generate_arc_summary_from_chapters(chapter_summaries: List[Dict[str, Any]], arc_name: str = "") -> Optional[str]:
    """Từ danh sách tóm tắt chương, AI tạo tóm tắt ngắn cho Arc."""
    if not chapter_summaries or not isinstance(chapter_summaries, list):
        return None
    parts = []
    for i, ch in enumerate(chapter_summaries):
        num = ch.get("chapter_number") or ch.get("num") or (i + 1)
        summ = ch.get("summary") or ch.get("description") or ""
        if summ:
            parts.append(f"Chương {num}: {summ}")
    if not parts:
        return None
    combined = "\n".join(parts)
    try:
        model = _get_default_tool_model()
        prompt = f"""Các tóm tắt chương thuộc Arc '{arc_name or "Unnamed"}':

{combined}

Nhiệm vụ: Viết 1 đoạn tóm tắt ngắn gọn (2-5 câu) cho toàn bộ Arc. Chỉ trả về đoạn tóm tắt, không lời dẫn."""
        resp = AIService.call_openrouter(
            messages=[{"role": "user", "content": prompt}],
            model=model,
            temperature=0.3,
            max_tokens=500,
        )
        raw = (resp.choices[0].message.content or "").strip()
        return raw if raw else None
    except Exception as e:
        print(f"generate_arc_summary_from_chapters error: {e}")
        return None


def generate_chapter_metadata(content: str) -> Dict[str, str]:
    """Dùng model để tóm tắt và phân tích art_style. Trả về {"summary": "...", "art_style": "..."}."""
    if not content or not str(content).strip():
        return {"summary": "", "art_style": ""}
    try:
        model = _get_default_tool_model()
        prompt = f"""Phân tích đoạn văn/chương sau và trả về ĐÚNG MỘT JSON với 2 key:
- "summary": Tóm tắt nội dung (2-4 câu, tiếng Việt).
- "art_style": Phong cách viết (1-2 câu).

NỘI DUNG:
{content[:12000]}

Chỉ trả về JSON."""
        response = AIService.call_openrouter(
            messages=[{"role": "user", "content": prompt}],
            model=model,
            temperature=0.2,
            max_tokens=500,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content
        raw = AIService.clean_json_text(raw)
        data = json.loads(raw)
        return {
            "summary": str(data.get("summary", ""))[:2000],
            "art_style": str(data.get("art_style", ""))[:500],
        }
    except Exception as e:
        print(f"generate_chapter_metadata error: {e}")
        return {"summary": "", "art_style": ""}


def extract_timeline_events_from_content(content: str, chapter_label: str = "") -> List[Dict[str, Any]]:
    """AI trích xuất sự kiện timeline từ nội dung chương."""
    if not content or not str(content).strip():
        return []
    try:
        model = _get_default_tool_model()
        ctx = f"Chương: {chapter_label}\n\n" if chapter_label else ""
        prompt = f"""Trích xuất các SỰ KIỆN theo thứ tự thời gian từ nội dung dưới đây. Mỗi sự kiện: event_order (1,2,...), title, description, raw_date, event_type (event|flashback|milestone|timeskip|other).

{ctx}NỘI DUNG:
{content[:25000]}

Trả về ĐÚNG MỘT JSON với key "events" là mảng. Nếu không có sự kiện rõ ràng, trả về {{ "events": [] }}. Chỉ trả về JSON."""
        response = AIService.call_openrouter(
            messages=[{"role": "user", "content": prompt}],
            model=model,
            temperature=0.2,
            max_tokens=4000,
            response_format={"type": "json_object"},
        )
        raw = (response.choices[0].message.content or "").strip()
        raw = AIService.clean_json_text(raw)
        data = json.loads(raw)
        events = data.get("events") if isinstance(data, dict) else []
        if not isinstance(events, list):
            return []
        out = []
        for i, e in enumerate(events):
            if not isinstance(e, dict):
                continue
            order = int(e.get("event_order", i + 1))
            title = str(e.get("title", "")).strip() or f"Sự kiện {order}"
            desc = str(e.get("description", ""))[:2000]
            raw_date = str(e.get("raw_date", ""))[:200]
            etype = str(e.get("event_type", "event")).lower()
            if etype not in ("event", "flashback", "milestone", "timeskip", "other"):
                etype = "event"
            out.append({
                "event_order": order,
                "title": title,
                "description": desc,
                "raw_date": raw_date,
                "event_type": etype,
            })
        return out
    except Exception as ex:
        print(f"extract_timeline_events_from_content error: {ex}")
        return []


def get_file_sample(file_content: str, sample_size: int = 80) -> str:
    """Lấy mẫu: 80 dòng đầu + 80 giữa + 80 cuối."""
    if not file_content or not str(file_content).strip():
        return ""
    lines = str(file_content).strip().splitlines()
    total_lines = len(lines)
    if total_lines <= sample_size * 3:
        return "\n".join(lines)
    parts = []
    parts.append(f"[ĐẦU FILE - {sample_size} dòng đầu]")
    parts.append("\n".join(lines[:sample_size]))
    mid_start = total_lines // 2 - sample_size // 2
    parts.append(f"\n\n[GIỮA FILE - {sample_size} dòng giữa]")
    parts.append("\n".join(lines[mid_start:mid_start + sample_size]))
    parts.append(f"\n\n[CUỐI FILE - {sample_size} dòng cuối]")
    parts.append("\n".join(lines[-sample_size:]))
    return "\n".join(parts)


def analyze_split_strategy(
    file_content: str,
    file_type: str = "story",
    context_hint: str = "",
) -> Dict[str, Any]:
    """AI phân tích mẫu file để tìm quy luật phân cách. Trả về {"split_type": "...", "split_value": "..."}."""
    if not file_content or not str(file_content).strip():
        return {"split_type": "by_length", "split_value": "2000"}
    sample = get_file_sample(file_content, sample_size=80)
    try:
        model = _get_default_tool_model()
        type_hints = {
            "story": "Truyện - tìm quy luật phân cách chương.",
            "character_data": "Dữ liệu nhân vật - tìm quy luật phân cách entity.",
            "excel_export": "Excel/CSV - cắt theo Sheet hoặc Row count.",
        }
        hint_text = type_hints.get(file_type.strip().lower(), type_hints["story"])
        if context_hint:
            hint_text += f"\nGợi ý: {context_hint}"
        prompt = f"""Phân tích mẫu file và TÌM QUY LUẬT PHÂN CÁCH.

Loại file: {hint_text}

MẪU FILE:
---
{sample}
---

Trả về ĐÚNG MỘT JSON:
- "split_type": "by_keyword" | "by_length" | "by_sheet"
- "split_value": regex/keyword hoặc số ký tự

Ví dụ: {{"split_type": "by_keyword", "split_value": "^Chương\\\\s+\\\\d+"}}
Chỉ trả về JSON."""
        response = AIService.call_openrouter(
            messages=[{"role": "user", "content": prompt}],
            model=model,
            temperature=0.2,
            max_tokens=500,
            response_format={"type": "json_object"},
        )
        raw = (response.choices[0].message.content or "").strip()
        raw = AIService.clean_json_text(raw)
        data = json.loads(raw)
        split_type = data.get("split_type", "by_length")
        split_value = str(data.get("split_value", "2000")).strip()
        if split_type not in ["by_keyword", "by_length", "by_sheet"]:
            split_type = "by_length"
        return {"split_type": split_type, "split_value": split_value}
    except Exception as e:
        print(f"analyze_split_strategy error: {e}")
        return {"split_type": "by_length", "split_value": "2000"}


def _build_smart_regex_pattern(keyword: str) -> str:
    """Xây regex hỗ trợ có dấu/không dấu."""
    keyword_upper = keyword.upper().strip()
    if keyword_upper in ["CHƯƠNG", "CHUONG", "CHAPTER"]:
        return r"(?i)(CHƯƠNG|CHUONG|CHAPTER)\s+\d+[:\s-]*"
    elif keyword_upper in ["PHẦN", "PHAN", "PART"]:
        return r"(?i)(PHẦN|PHAN|PART)\s+\d+[:\s-]*"
    elif keyword_upper in ["---", "***", "==="]:
        return rf"(?i)\s*{re.escape(keyword)}\s*"
    else:
        return rf"(?i)^\s*{re.escape(keyword)}\s*"


def execute_split_logic(
    file_content: str,
    split_type: str,
    split_value: str,
    debug: bool = False,
) -> List[Dict[str, Any]]:
    """Cắt file bằng Python. Trả về list of {"title": str, "content": str, "order": int}."""
    if not file_content or not str(file_content).strip():
        return []
    content = str(file_content).strip()
    out = []
    try:
        if split_type == "by_keyword":
            pattern_str = split_value.strip() or "---"
            is_regex = any(c in pattern_str for c in ["^", "$", "\\d", "\\s", "\\w", "\\+", "\\*", "\\?", "\\[", "\\(", "\\{"])
            if not is_regex:
                pattern_str = _build_smart_regex_pattern(pattern_str)
            try:
                pattern = re.compile(pattern_str, re.IGNORECASE | re.MULTILINE)
            except Exception:
                pattern_str = rf"^\s*{re.escape(split_value.strip())}\s*"
                pattern = re.compile(pattern_str, re.IGNORECASE | re.MULTILINE)
            matches = list(pattern.finditer(content))
            if len(matches) == 0:
                return []
            if matches[0].start() > 0:
                part_content = content[0:matches[0].start()].strip()
                if part_content:
                    out.append({"title": "Phần mở đầu", "content": part_content, "order": 1})
            for i, match in enumerate(matches):
                start = match.end()
                end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
                part_content = content[start:end].strip()
                if not part_content:
                    continue
                title = match.group(0).strip()[:50] if match.group(0) else f"Phần {len(out)+1}"
                if not title or len(title.strip()) < 2:
                    first_line = part_content.splitlines()[0] if part_content.splitlines() else ""
                    title = first_line[:50] if first_line else f"Phần {len(out)+1}"
                out.append({"title": title, "content": part_content, "order": len(out) + 1})
        elif split_type == "by_length":
            chunk_size = int(split_value) if split_value.isdigit() else 2000
            chunk_size = max(500, min(chunk_size, 50000))
            lines = content.splitlines()
            current_chunk = []
            current_len = 0
            chunk_num = 1
            for line in lines:
                line_len = len(line) + 1
                if current_len + line_len > chunk_size and current_chunk:
                    chunk_text = "\n".join(current_chunk).strip()
                    if chunk_text:
                        out.append({"title": f"Phần {chunk_num}", "content": chunk_text, "order": chunk_num})
                        chunk_num += 1
                    current_chunk = [line]
                    current_len = line_len
                else:
                    current_chunk.append(line)
                    current_len += line_len
            if current_chunk:
                chunk_text = "\n".join(current_chunk).strip()
                if chunk_text:
                    out.append({"title": f"Phần {chunk_num}", "content": chunk_text, "order": chunk_num})
        elif split_type == "by_sheet":
            if split_value.lower() == "row count" or split_value.isdigit():
                row_count = int(split_value) if split_value.isdigit() else 100
                lines = content.splitlines()
                for i in range(0, len(lines), row_count):
                    chunk_lines = lines[i:i + row_count]
                    if chunk_lines:
                        out.append({"title": f"Sheet {i // row_count + 1}", "content": "\n".join(chunk_lines), "order": i // row_count + 1})
            else:
                out.append({"title": "Phần 1", "content": content, "order": 1})
        else:
            out.append({"title": "Phần 1", "content": content, "order": 1})
        return out
    except Exception as e:
        print(f"execute_split_logic error: {e}")
        return [{"title": "Phần 1", "content": content, "order": 1}]
