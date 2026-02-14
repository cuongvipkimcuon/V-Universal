# ai/rule_mining.py - RuleMiningSystem
import json
from typing import Dict, List, Optional, Tuple

from ai.hybrid_search import HybridSearch
from ai.service import AIService, _get_default_tool_model


def _similar_rules_with_scores(new_rule_content: str, project_id: str, top_k: int = 8) -> List[Dict]:
    """Lấy danh sách luật tương tự trong DB kèm % giống (0-100). Chỉ lấy entity [RULE]."""
    raw_list = HybridSearch.smart_search_hybrid_raw(new_rule_content, project_id, top_k=top_k)
    out = []
    for item in (raw_list or []):
        name = (item.get("entity_name") or "").strip()
        if "[RULE]" not in name.upper():
            continue
        desc = (item.get("description") or "").strip()
        if not desc:
            continue
        sim = item.get("similarity")
        if sim is None:
            sim = 0.5
        try:
            sim = float(sim)
        except (TypeError, ValueError):
            sim = 0.5
        sim = max(0.0, min(1.0, sim))
        out.append({
            "entity_name": name,
            "content": desc[:2000],
            "similarity_pct": int(round(sim * 100)),
        })
    return out[:5]


class RuleMiningSystem:
    """Hệ thống khai thác và quản lý luật từ chat"""

    @staticmethod
    def extract_rule_raw(user_prompt: str, ai_response: str) -> Optional[str]:
        """Trích xuất 1 luật thô (giữ tương thích). Gọi extract_rules_raw lấy phần tử đầu."""
        rules = RuleMiningSystem.extract_rules_raw(user_prompt, ai_response)
        return rules[0] if rules else None

    @staticmethod
    def extract_rules_raw(user_prompt: str, ai_response: str) -> List[str]:
        """Trích xuất không hoặc nhiều luật từ một câu chat. Trả về list các câu luật (có thể 0, 1 hoặc nhiều)."""
        prompt = f"""
Bạn là "Trinh Sát Luật" (Rule Scout). Nhiệm vụ: Phát hiện sở thích/yêu cầu của User trong hội thoại.

HỘI THOẠI:
- User: "{user_prompt[:2000]}"
- AI: (Phản hồi) {ai_response[:1500] if ai_response else "(không có)"}

MỤC TIÊU:
Phát hiện User có đang ngầm chỉ định CÁCH LÀM VIỆC, CÁCH VIẾT, hoặc ĐỊNH DẠNG không.
MỘT CÂU CHAT CÓ THỂ CHỨA NHIỀU LUẬT (ví dụ: "luôn dùng JSON và đừng viết code" → 2 luật).

TIÊU CHÍ (độ nhạy cao):
1. Yêu cầu định dạng: "chỉ json", "dùng markdown", "đừng viết code", "viết ngắn thôi".
2. Điều chỉnh văn phong: "nghiêm túc hơn", "bớt nói nhảm", "dùng tiếng Việt".
3. Sửa lỗi: "sai rồi", "không phải thế", "làm thế này mới đúng".

OUTPUT:
- Trả về ĐÚNG MỘT JSON với key "rules": mảng chuỗi. Mỗi phần tử là 1 câu luật ngắn gọn (Tiếng Việt).
- Nếu không phát hiện luật nào: "rules": []
- Ví dụ: {{ "rules": ["Luôn trả về JSON khi được yêu cầu", "Không giải thích dài khi user khó chịu"] }}

Chỉ trả về JSON, không giải thích.
"""
        messages = [
            {"role": "system", "content": "You are Rule Extractor. Return only JSON with key 'rules' (array of strings)."},
            {"role": "user", "content": prompt}
        ]
        try:
            response = AIService.call_openrouter(
                messages=messages,
                model=_get_default_tool_model(),
                temperature=0.3,
                max_tokens=800,
                response_format={"type": "json_object"},
            )
            text = (response.choices[0].message.content or "").strip()
            text = AIService.clean_json_text(text)
            data = json.loads(text)
            rules_in = data.get("rules")
            if not isinstance(rules_in, list):
                return []
            out = []
            for r in rules_in:
                s = (r if isinstance(r, str) else str(r)).strip()
                if s and "NO_RULE" not in s and len(s) >= 5:
                    out.append(s)
            return out
        except Exception as e:
            print(f"Rule extraction error: {e}")
            return []

    @staticmethod
    def analyze_rule_conflict(new_rule_content: str, project_id: str) -> Dict:
        """Kiểm tra trùng/conflict với DB. Trả về similar_rules (list có similarity_pct) để user xác nhận."""
        similar_rules = _similar_rules_with_scores(new_rule_content, project_id, top_k=8)
        if not similar_rules:
            return {
                "status": "NEW",
                "reason": "Không tìm thấy luật tương tự trong DB.",
                "existing_rule_summary": "None",
                "merged_content": None,
                "suggested_content": new_rule_content,
                "similar_rules": [],
            }
        best = similar_rules[0]
        best_pct = best.get("similarity_pct", 0)
        similar_rules_str = "\n".join([
            f"- [{x.get('entity_name', '')}] ({x.get('similarity_pct', 0)}% giống): {x.get('content', '')[:300]}"
            for x in similar_rules
        ])
        judge_prompt = f"""
Luật mới (đề xuất): "{new_rule_content}"

Các luật trong DB có nội dung tương tự (kèm % giống):
{similar_rules_str}

Nhiệm vụ: So sánh luật mới với từng luật trên.
- CONFLICT: Mâu thuẫn trực tiếp (cũ bảo A, mới bảo không A).
- MERGE: Cùng chủ đề, luật mới chi tiết hơn hoặc bổ sung → gợi ý nội dung gộp.
- NEW: Chủ đề khác hẳn (dù % giống có thể > 0).

Trả về ĐÚNG MỘT JSON:
{{
    "status": "CONFLICT" | "MERGE" | "NEW",
    "existing_rule_summary": "Tóm tắt luật cũ (hoặc luật giống nhất) bằng tiếng Việt",
    "reason": "Lý do ngắn gọn",
    "merged_content": "Nội dung luật đã gộp hoàn chỉnh (chỉ khi status=MERGE), else null"
}}
"""
        try:
            response = AIService.call_openrouter(
                messages=[
                    {"role": "system", "content": "You are Rule Judge. Return only JSON."},
                    {"role": "user", "content": judge_prompt}
                ],
                model=_get_default_tool_model(),
                temperature=0.2,
                max_tokens=4000,
                response_format={"type": "json_object"}
            )
            content = AIService.clean_json_text(response.choices[0].message.content or "{}")
            result = json.loads(content)
            return {
                "status": result.get("status", "NEW"),
                "reason": result.get("reason", "Không có lý do"),
                "existing_rule_summary": result.get("existing_rule_summary", best.get("content", "")[:200]),
                "merged_content": result.get("merged_content"),
                "suggested_content": new_rule_content,
                "similar_rules": similar_rules,
            }
        except Exception as e:
            print(f"Rule analysis error: {e}")
            return {
                "status": "NEW",
                "reason": str(e),
                "existing_rule_summary": "Lỗi phân tích",
                "merged_content": None,
                "suggested_content": new_rule_content,
                "similar_rules": similar_rules,
            }

    @staticmethod
    def crystallize_session(chat_history: List[Dict], persona_role: str) -> str:
        """Tóm tắt và lọc thông tin giá trị từ chat history"""
        chat_text = "\n".join([f"{m.get('role', '')}: {m.get('content', '')}" for m in chat_history])
        prompt = f"""
        Bạn là Thư Ký Cuộc Họp ({persona_role}).
        Nhiệm vụ: Đọc chat và LỌC BỎ VÔ NGHĨA. Chỉ giữ lại và TÓM TẮT thông tin giá trị.

        CHAT LOG: {chat_text}

        OUTPUT: Trả về bản tóm tắt súc tích (50-100 từ) bằng Tiếng Việt. Nếu toàn chào hỏi, trả về "NO_INFO".
        """
        try:
            response = AIService.call_openrouter(
                messages=[
                    {"role": "system", "content": "You are Conversation Summarizer. Return text only."},
                    {"role": "user", "content": prompt}
                ],
                model=_get_default_tool_model(),
                temperature=0.3,
                max_tokens=8000
            )
            return (response.choices[0].message.content or "").strip()
        except Exception as e:
            print(f"Crystallize error: {e}")
            return f"AI Error: {e}"
