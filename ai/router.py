# ai/router.py - is_multi_*, get_v7_reminder_message, SmartAIRouter
import json
import re
from typing import Dict

from config import Config

from ai.context_helpers import get_mandatory_rules, get_rules_by_type_block, get_rules_for_intent_prompt
from ai.context_schema import (
    infer_default_context_needs,
    normalize_context_needs,
    normalize_context_priority,
)
from ai.service import AIService, _get_default_tool_model
from ai.utils import (
    cap_chat_history_to_tokens,
    get_bible_index,
    get_chapter_list_for_router,
    get_project_overview,
)


def is_multi_step_update_data_request(query: str) -> bool:
    """
    Bộ lọc nhỏ: phát hiện câu hỏi có yêu cầu 2+ thao tác update_data (extract/update/delete bible, relation, timeline, chunking).
    Dùng cho V6: nếu True thì không thực hiện mà cảnh báo user bật V7.
    """
    if not query or not isinstance(query, str):
        return False
    q = query.strip().lower()
    if len(q) < 3:
        return False
    multi_phrases = [
        "tất cả", "toàn bộ", "cả 4", "cả bốn", "full", "mọi bước", "tất cả các bước",
        "data analyze", "phân tích đầy đủ", "4 bước", "bốn bước", "bible và relation",
        "relation và timeline", "timeline và chunk", "bible, relation", "relation, timeline",
        "extract bible và", "trích xuất bible và", "chạy đủ", "làm đủ", "thực hiện đủ",
    ]
    for phrase in multi_phrases:
        if phrase in q:
            return True
    targets = ["bible", "relation", "timeline", "chunking"]
    if sum(1 for t in targets if t in q) >= 2:
        return True
    return False


def is_multi_intent_request(query: str) -> bool:
    """
    Bộ lọc: phát hiện câu hỏi có vẻ cần nhiều intent (nhiều bước xử lý khác nhau) để trả lời đủ.
    Dùng cho V6: hiển thị lời nhắc bật V7 khi True.
    """
    if not query or not isinstance(query, str):
        return False
    q = query.strip().lower()
    if len(q) < 5:
        return False
    multi_intent_phrases = [
        " rồi ", " sau đó ", " xong thì ", " xong rồi ", " rồi so sánh", " rồi tìm ",
        " rồi kiểm tra", " rồi trích ", " và so sánh", " và tìm ", " và kiểm tra", " và trích ",
        " tóm tắt rồi", " tóm tắt và ", " trích xuất rồi", " trích xuất và ", " extract rồi",
        " extract và ", " kiểm tra .* và ", " vừa .* vừa ", " đồng thời ",
        " kết hợp với timeline", " kết hợp với bible", " so sánh với timeline",
        " đối chiếu với ", " rồi đối chiếu", " sau khi .* thì ",
    ]
    for phrase in multi_intent_phrases:
        if ".*" in phrase:
            if re.search(phrase.replace(".*", r".{2,40}"), q):
                return True
        elif phrase in q:
            return True
    return False


def get_v7_reminder_message() -> str:
    """Lời nhắc thống nhất khi V6 phát hiện câu hỏi cần nhiều bước / nhiều intent."""
    return (
        "**Yêu cầu của bạn có vẻ gồm nhiều thao tác hoặc nhiều bước xử lý** (nhiều intent). "
        "Chế độ V6 chỉ xử lý **một** intent mỗi lần. "
        "Vui lòng **bật V7 Planner** (trong cài đặt Chat) để thực hiện nhiều bước trong một lần."
    )


# Intent không cần dữ liệu từ DB để trả lời — có thể thực thi luôn bằng một lần gọi LLM.
# unified: thao tác theo chương (job nền), không cần context_planner.
INTENTS_NO_DATA = frozenset({"chat_casual", "ask_user_clarification", "web_search", "suggest_v7", "unified"})

# V8: Bảng ánh xạ intent → handler. Ghi nhớ luật / "từ giờ luật là" → chat_casual (không còn update_data).
INTENT_HANDLER_MAP = {
    "ask_user_clarification": "clarification",
    "suggest_v7": "template",
    "web_search": "llm_casual",
    "chat_casual": "llm_casual",
    "search_context": "llm_with_context",
    "query_Sql": "llm_with_context",
    "numerical_calculation": "llm_with_context",
    "check_chapter_logic": "llm_with_context",
    # V7: intent đặc biệt cho phân tích nhiều chương (executor xử lý nhiều sub-range bên trong),
    # vẫn dùng handler llm_with_context cho từng sub-range.
    "multi_chapter_analysis": "llm_with_context",
    "unified": "data_operation",
}


class SmartAIRouter:
    """Bộ định tuyến AI thông minh với hybrid search và bible index"""

    # --- Mô hình 3 bước: (1) Intent only (2) Context planner khi cần data (3) LLM trả lời ---

    @staticmethod
    def intent_only_classifier(user_prompt: str, chat_history_text: str, project_id: str = None) -> Dict:
        """
        Bước 1: Xem xét câu hỏi + QUY TẮC DỰ ÁN để đưa ra intent, lọc rule liên quan
        VÀ phát hiện các "luật" mới user vừa nêu (cách tương tác, quy ước, format, phong cách trả lời...).
        Trả về: intent, needs_data, rewritten_query, clarification_question, relevant_rules (chuỗi các quy tắc liên quan, dùng cho bước 2/3),
        và new_rules (mảng các câu luật mới trích xuất từ lượt chat này).
        """
        chat_history_text = cap_chat_history_to_tokens(chat_history_text or "")
        rules_context = ""
        project_name = "(Không có project)"
        chapter_list_str = "(Trống)"
        arc_chapters_summary = "(Trống)"
        if project_id:
            # Hai block: Method+Unknown (flow) và Info (chọn relevant_rules) + bối cảnh project + arc & chương
            try:
                method_flow_block = get_rules_by_type_block(project_id, None, ["Method", "Unknown"])
                info_rules_block = get_rules_for_intent_prompt(project_id)
            except Exception:
                method_flow_block = ""
                info_rules_block = ""
            try:
                overview = get_project_overview(project_id or "", bible_max_tokens=0)
                project_name = overview.get("project_name") or "(Không tên)"
                chapter_list_str = overview.get("chapter_list_str") or "(Trống)"
                arc_chapters_summary = overview.get("arc_chapters_summary") or "(Trống)"
            except Exception:
                pass
        else:
            method_flow_block = ""
            info_rules_block = ""
        method_block_str = (method_flow_block or "(Không có)").strip()[:2500]
        info_block_str = (info_rules_block or "(Không có)").strip()[:4000]
        prompt = f"""Bạn là bộ phân loại Intent và Trinh Sát Luật (Rule Scout). Nhiệm vụ:
1) Xác định intent của user từ câu hỏi.
2) Trong block QUY TẮC DỰ ÁN (INFO RULES) dưới đây, chọn ra những quy tắc LIÊN QUAN đến câu hỏi (relevant_rules) — CHỈ chọn từ block INFO RULES, copy nguyên dạng "- ...". Không chọn từ block METHOD RULES.
3) Phát hiện mọi "LUẬT" mới mà user vừa nói ra trong lượt chat này: yêu cầu về cách tương tác, quy ước, cách trả lời, phong cách, định dạng, ràng buộc ("không được...", "luôn...", "từ giờ...", "hãy...").
4) Hiểu rõ bối cảnh DỰ ÁN (tên, arc, chương thuộc arc) để phân loại chính xác; khi user hỏi về một arc, khoanh vùng chapter_range theo các chương thuộc arc đó.

--- BỐI CẢNH DỰ ÁN ---
- TÊN DỰ ÁN: {project_name}
- ARC VÀ CHƯƠNG THUỘC ARC: {arc_chapters_summary}
- DANH SÁCH CHƯƠNG (số - tên): {chapter_list_str}
Lưu ý: Nếu câu hỏi nhắc đến arc/chương trùng với danh sách trên, hãy NGẦM HIỂU và khoanh vùng chapter_range tương ứng (vd. "arc Tuổi thơ" -> chapter_range = [1, 3] nếu arc đó gồm chương 1,2,3). KHÔNG cần hỏi lại trừ khi thật sự mơ hồ.

--- QUY TẮC LUỒNG (METHOD RULES) ---
{method_block_str}
(Chỉ tham khảo flow/cách lấy dữ liệu; KHÔNG chọn relevant_rules từ block này.)

--- QUY TẮC DỰ ÁN (INFO RULES) ---
{info_block_str}
(Chọn relevant_rules CHỈ từ block này, copy nguyên dạng "- ...".)

--- LỊCH SỬ CHAT (tham khảo) ---
{chat_history_text[:1500] if chat_history_text else "(Trống)"}

CÁC INTENT:
- ask_user_clarification: Câu quá ngắn, mơ hồ, thiếu chủ ngữ. Cần điền clarification_question (câu gợi ý hỏi lại).
- web_search: Cần thông tin thực tế bên ngoài (tỷ giá, tin tức, thời tiết, tra cứu).
- chat_casual: Chào hỏi, xã giao, cảm ơn, không yêu cầu tra cứu hay dữ liệu. Nếu câu chỉ là than vãn cảm xúc (buồn, mệt, chán, stress, cô đơn, nản, tuyệt vọng, "tụt mood"...) và không có yêu cầu tra cứu nội dung dự án (nhân vật, chương, timeline, dữ liệu...), hãy chọn intent = "chat_casual".
- suggest_v7: Câu rõ ràng cần 2+ bước (vd "tóm tắt chương 1 rồi so sánh timeline").
  Đặc biệt, nếu user yêu cầu phân tích/tổng hợp trên một khoảng chương RẤT RỘNG (ví dụ: "chương 1 đến 30", "từ chương 5-40") với nhiều ý phân tích (so sánh, tìm logic hole, thống kê...), hãy ưu tiên chọn intent = "suggest_v7" thay vì chỉ search_context/unified, để V7 Planner chia nhỏ khoảng chương và gom kết quả.
- search_context: Tra cứu nội dung dự án (nhân vật, quan hệ, timeline, tóm tắt chương, nội dung chương).
- query_Sql: User muốn XEM/LIỆT KÊ dữ liệu thô (list chương, luật, timeline dạng bảng).
- unified: Ra lệnh chạy phân tích/trích xuất theo chương (Unified: Bible + Timeline + Chunks + Relations). Bắt buộc có chapter_range (ví dụ chương 1, chương 1 đến 5). Không dùng cho "ghi nhớ quy tắc".
- chat_casual (bổ sung): Ghi nhớ quy tắc / ưu tiên / "từ giờ luật là", "hãy nhớ rằng", "V hãy nghiêm khắc khi..." → chọn chat_casual (luật vẫn được trích trong new_rules).
- check_chapter_logic: Hỏi lỗi logic/mâu thuẫn/plot hole của chương.
- numerical_calculation: Tính toán, thống kê trên dữ liệu.

NGUYÊN TẮC BỔ SUNG:
- Nếu câu KHÔNG nhắc trực tiếp hay gián tiếp tới nội dung/vấn đề của dự án (không nói về chương, nhân vật, timeline, dữ liệu, lỗi logic...) và chỉ là than vãn cảm xúc hoặc nói chuyện chung, ưu tiên phân loại intent = "chat_casual". Chỉ khi nội dung thật sự mơ hồ mà không thể hiểu được ý user thì mới chọn "ask_user_clarification".

INPUT USER: "{user_prompt}"

Trả về JSON với đủ key:
- intent: một trong các intent trên
- rewritten_query: câu viết lại ngắn
- clarification_question: chỉ khi intent=ask_user_clarification
- relevant_rules: chuỗi chỉ gồm các quy tắc LIÊN QUAN đến câu hỏi (copy nguyên định dạng "- ..." từ QUY TẮC DỰ ÁN). Nếu không có quy tắc nào liên quan hoặc không có quy tắc thì trả về chuỗi rỗng "".
- new_rules: mảng các câu luật user vừa đặt ra trong lượt chat này (có thể rỗng nếu không có). Mỗi phần tử là 1 câu ngắn gọn, dạng khẳng định (vd: "Luôn trả lời bằng tiếng Việt.", "Không được chửi user.", "Ưu tiên tóm tắt ngắn gọn.").
"""
        try:
            response = AIService.call_openrouter(
                messages=[
                    {
                        "role": "system",
                        "content": "Bạn là bộ phân loại Intent & Rule Scout. Trả về JSON: intent, rewritten_query, clarification_question, relevant_rules (chuỗi các quy tắc liên quan), new_rules (mảng các câu luật mới của user, có thể rỗng).",
                    },
                    {"role": "user", "content": prompt},
                ],
                model=_get_default_tool_model(),
                temperature=0.1,
                max_tokens=500,
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content
            content = AIService.clean_json_text(content)
            data = json.loads(content)
            intent = (data.get("intent") or "chat_casual").strip().lower()
            if intent not in (
                "ask_user_clarification", "web_search", "chat_casual", "suggest_v7",
                "search_context", "query_Sql", "unified", "check_chapter_logic", "numerical_calculation",
            ):
                intent = "chat_casual"
            # Legacy: LLM trả về update_data → coi là unified (chỉ thao tác theo chương).
            if intent == "update_data":
                intent = "unified"
            needs_data = intent not in INTENTS_NO_DATA
            relevant_rules = (data.get("relevant_rules") or "").strip()
            raw_new_rules = data.get("new_rules") or []
            new_rules: list[str] = []
            if isinstance(raw_new_rules, list):
                for r in raw_new_rules:
                    s = (r if isinstance(r, str) else str(r)).strip()
                    if s and len(s) >= 5 and "NO_RULE" not in s.upper():
                        if s not in new_rules:
                            new_rules.append(s)
            return {
                "intent": intent,
                "needs_data": needs_data,
                "rewritten_query": (data.get("rewritten_query") or user_prompt).strip(),
                "clarification_question": (data.get("clarification_question") or "").strip(),
                "relevant_rules": relevant_rules,
                "new_rules": new_rules,
            }
        except Exception as e:
            print(f"Intent classifier error: {e}")
            return {
                "intent": "chat_casual",
                "needs_data": False,
                "rewritten_query": user_prompt,
                "clarification_question": "",
                "relevant_rules": "",
                "new_rules": [],
            }

    @staticmethod
    def context_planner(
        user_prompt: str,
        intent: str,
        chat_history_text: str,
        project_id: str = None,
        relevant_rules: str = "",
    ) -> Dict:
        """
        Bước 2: Nhìn tổng quan DB (project name, arcs, chapters, bible, relation/timeline/chunks)
        và quy tắc liên quan từ bước 1; quyết định cần lấy dữ liệu nào và luật nào.
        Trả về router_result đủ để gọi ContextManager.build_context (có included_rules_text).
        """
        overview = get_project_overview(project_id or "", bible_max_tokens=2000)
        project_name = overview.get("project_name") or "(Không tên)"
        arcs_summary = overview.get("arcs_summary") or "(Trống)"
        arc_chapters_summary = overview.get("arc_chapters_summary") or "(Trống)"
        chapter_list_str = overview.get("chapter_list_str") or "(Trống)"
        bible_index = overview.get("bible_index") or "(Trống)"
        relation_summary = overview.get("relation_summary") or "0 quan hệ"
        timeline_summary = overview.get("timeline_summary") or "0 sự kiện"
        chunks_summary = overview.get("chunks_summary") or "0 chunks"
        try:
            prefix_setup = Config.get_prefix_setup()
            prefix_setup_str = "\n".join(
                f"- [{p.get('prefix_key', '')}]: {p.get('description', '')}" for p in (prefix_setup or [])
            ) if prefix_setup else "(Chưa cấu hình Bible Prefix.)"
        except Exception:
            prefix_setup_str = "(Chưa cấu hình Bible Prefix.)"
        chat_capped = cap_chat_history_to_tokens(chat_history_text or "")
        relevant_rules_block = (relevant_rules or "").strip() or "(Bước 1 không chọn quy tắc liên quan)"

        planner_prompt = f"""Bạn là Context Planner. Intent đã xác định: **{intent}**. Nhiệm vụ: dựa vào BỨC TRANH TỔNG QUAN dữ liệu dự án dưới đây, quyết định (1) cần LẤY DỮ LIỆU NÀO từ DB (bible, chapter, timeline, relation, chunk), (2) cần đưa LUẬT NÀO vào context (từ các quy tắc liên quan bước 1 đã lọc), (3) chọn rõ các TARGET theo từng nguồn (tên entity trong Bible, từ khóa chunk, entity để xem quan hệ, từ khóa timeline). Trả về JSON.

QUY TẮC CỰC KỲ QUAN TRỌNG VỀ CHAPTER RANGE (KHÔNG ĐƯỢC VI PHẠM):
- TUYỆT ĐỐI KHÔNG tự ý bịa hoặc suy đoán chương khi USER KHÔNG nói rõ chương / khoảng chương / số chương.
- CHỈ đặt chapter_range khi:
  (a) User nói RÕ ràng “chương X”, “Chap Y”, “chương X đến Y”, “3 chương đầu”, “chương mới nhất”; HOẶC
  (b) User nói tới MỘT ARC cụ thể (vd: “arc Tuổi thơ”) và ARC ĐÓ đã có danh sách chương trong phần ARC VÀ CHƯƠNG THUỘC ARC.
- Trong MỌI trường hợp khác (hỏi nhân vật, quan hệ, phân tích chung, không nhắc tới chương hay arc cụ thể) thì BẮT BUỘC phải để:
  chapter_range = null
  chapter_range_mode = null
- Ví dụ: câu hỏi “Võ Quốc Thanh là ai?” chỉ là tra cứu nhân vật → KHÔNG được đặt chapter_range (để null), dù bạn có biết nhân vật xuất hiện ở chương nào.

--- BỨC TRANH TỔNG QUAN DỮ LIỆU DỰ ÁN ---
- TÊN DỰ ÁN: {project_name}
- ARCS: {arcs_summary}
- ARC VÀ CHƯƠNG THUỘC ARC: {arc_chapters_summary}
- DANH SÁCH CHƯƠNG (số - tên): {chapter_list_str}
- BIBLE (entity): {bible_index[:2000] if bible_index else "(Trống)"}
- RELATION: {relation_summary}
- TIMELINE: {timeline_summary}
- CHUNKS: {chunks_summary}
- PREFIX ENTITY: {prefix_setup_str}
- LỊCH SỬ CHAT: {chat_capped[:1000] if chat_capped else "(Trống)"}

--- QUY TẮC LIÊN QUAN (từ bước 1, đã lọc theo câu hỏi) ---
{relevant_rules_block[:2500] if relevant_rules_block else "(Không có)"}

INPUT USER: "{user_prompt}"

Bạn cần trả về:
- context_needs: mảng ["bible"] | ["relation"] | ["timeline"] | ["chunk"] | ["chapter"] hoặc kết hợp (cần lấy nguồn nào).
- context_priority: thứ tự ưu tiên (phần tử đầu quan trọng nhất).
- chapter_range: null hoặc [start, end]. "Chương 1" -> [1,1]; "chương 1 đến 5" -> [1,5]. Khi user hỏi về một ARC (vd. "arc Tuổi thơ"), dựa vào ARC VÀ CHƯƠNG THUỘC ARC để đặt chapter_range = [min, max] các chương thuộc arc đó.
- chapter_range_mode: "range" | "first" | "latest" | null.
- target_bible_entities: danh sách TÊN ENTITY trong Bible (mảng string). KHÔNG kèm prefix như [NV], [CHAR] — chỉ dùng phần tên sau khi bỏ prefix.
- target_chunk_keywords: mảng string, từ khóa/cụm từ để tìm chunk (nếu cần chunk).
- target_relation_entities: mảng string, entity chính để xem quan hệ (nếu cần relation).
- target_timeline_keywords: mảng string, từ khóa sự kiện/timeline (nếu cần timeline).
- query_target: chỉ khi intent=query_Sql: "chapters"|"rules"|"bible_entity"|"chunks"|"timeline"|"relation"|"summary"|"art".
- data_operation_type, data_operation_target: chỉ khi intent=unified.
- included_rules_text: chuỗi các quy tắc (từ block QUY TẮC LIÊN QUAN trên) thực sự cần đưa vào context để trả lời. Giữ nguyên định dạng "- ...". Có thể là toàn bộ hoặc subset. Nếu không cần quy tắc nào thì "".

Trả về JSON (đủ key):
        {{ "context_needs": [], "context_priority": [], "chapter_range": null, "chapter_range_mode": null, "chapter_range_count": 5, "target_bible_entities": [], "target_chunk_keywords": [], "target_relation_entities": [], "target_timeline_keywords": [], "inferred_prefixes": [], "rewritten_query": "", "query_target": "", "clarification_question": "", "data_operation_type": "", "data_operation_target": "", "update_summary": "", "included_rules_text": "" }}
Chỉ trả về JSON."""

        try:
            response = AIService.call_openrouter(
                messages=[
                    {"role": "system", "content": "Bạn là Context Planner. Dựa vào tổng quan DB và quy tắc liên quan, trả về JSON: context_needs, chapter_range, target_bible_entities, included_rules_text, ..."},
                    {"role": "user", "content": planner_prompt},
                ],
                model=_get_default_tool_model(),
                temperature=0.1,
                max_tokens=600,
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content
            content = AIService.clean_json_text(content)
            data = json.loads(content)
        except Exception as e:
            print(f"Context planner error: {e}")
            full = SmartAIRouter.ai_router_pro_v2(user_prompt, chat_history_text, project_id)
            full["intent"] = intent
            full["included_rules_text"] = relevant_rules if relevant_rules else None
            return full

        included_rules = (data.get("included_rules_text") or "").strip()
        if not included_rules and relevant_rules:
            included_rules = relevant_rules

        result = {
            "intent": intent,
            "context_needs": data.get("context_needs") if isinstance(data.get("context_needs"), list) else [],
            "context_priority": data.get("context_priority") if isinstance(data.get("context_priority"), list) else [],
            "chapter_range": data.get("chapter_range"),
            "chapter_range_mode": data.get("chapter_range_mode") or None,
            "chapter_range_count": int(data.get("chapter_range_count", 5)),
            "target_files": [],
            "target_bible_entities": data.get("target_bible_entities") if isinstance(data.get("target_bible_entities"), list) else [],
            "target_chunk_keywords": data.get("target_chunk_keywords") if isinstance(data.get("target_chunk_keywords"), list) else [],
            "target_relation_entities": data.get("target_relation_entities") if isinstance(data.get("target_relation_entities"), list) else [],
            "target_timeline_keywords": data.get("target_timeline_keywords") if isinstance(data.get("target_timeline_keywords"), list) else [],
            "inferred_prefixes": data.get("inferred_prefixes") if isinstance(data.get("inferred_prefixes"), list) else [],
            "rewritten_query": (data.get("rewritten_query") or user_prompt).strip(),
            "reason": "Context planner",
            "clarification_question": (data.get("clarification_question") or "").strip(),
            "update_summary": (data.get("update_summary") or "").strip(),
            "data_operation_type": (data.get("data_operation_type") or "").strip(),
            "data_operation_target": (data.get("data_operation_target") or "").strip(),
            "query_target": (data.get("query_target") or "").strip(),
            "included_rules_text": included_rules if included_rules else None,
        }
        if intent == "search_context" and not result["context_needs"]:
            result["context_needs"] = infer_default_context_needs(result)
        result["context_priority"] = normalize_context_priority(result["context_priority"], result["context_needs"]) or list(result["context_needs"])
        result["context_needs"] = normalize_context_needs(result["context_needs"])
        valid_keys = Config.get_valid_prefix_keys()
        if valid_keys and result["inferred_prefixes"]:
            result["inferred_prefixes"] = [
                p for p in result["inferred_prefixes"]
                if p and str(p).strip().upper().replace(" ", "_") in valid_keys
            ]
        return result

    @staticmethod
    def ai_router_pro_v2(user_prompt: str, chat_history_text: str, project_id: str = None) -> Dict:
        """Router V2: Phân tích Intent và Target Files, có inject bible_index để nhận diện ý định."""
        chat_history_text = cap_chat_history_to_tokens(chat_history_text or "")
        rules_context = ""
        bible_index = ""
        prefix_setup_str = ""
        if project_id:
            rules_context = get_mandatory_rules(project_id)
            bible_index = get_bible_index(project_id, max_tokens=2000)
        try:
            prefix_setup = Config.get_prefix_setup()
            if prefix_setup:
                prefix_setup_str = "\n".join(
                    f"- [{p.get('prefix_key', '')}]: {p.get('description', '')}" for p in prefix_setup
                )
            else:
                prefix_setup_str = "(Chưa cấu hình loại thực thể trong Bible Prefix / bảng bible_prefix_config.)"
        except Exception:
            prefix_setup_str = "(Chưa cấu hình loại thực thể trong Bible Prefix.)"

        chapter_list_str = get_chapter_list_for_router(project_id) if project_id else "(Trống)"
        filter_multi = is_multi_step_update_data_request(user_prompt) or is_multi_intent_request(user_prompt)
        router_prompt = f"""
### VAI TRÒ
Bạn là AI Điều Phối Viên (Router) cho hệ thống V7-Universal. Nhiệm vụ của bạn là phân tích Input của User và quyết định công cụ (Intent) chính xác nhất để xử lý. Chỉ trả về JSON.

### 1. DỮ LIỆU ĐẦU VÀO
- QUY TẮC DỰ ÁN: {rules_context}
- BẢNG PREFIX ENTITY: {prefix_setup_str}
- DANH SÁCH ENTITY (Bible): {bible_index if bible_index else "(Trống)"}
- DANH SÁCH CHƯƠNG (số - tên): {chapter_list_str}
- LỊCH SỬ CHAT: {chat_history_text}
- REFERENCE (bộ lọc nhanh): Câu hỏi có thể cần **nhiều bước / nhiều intent**: {filter_multi}. Chỉ dùng làm tham khảo; bạn có quyền quyết định cuối.

### 2. BẢNG QUY TẮC CHỌN INTENT (ƯU TIÊN TỪ TRÊN XUỐNG)

| INTENT | ĐIỀU KIỆN KÍCH HOẠT (TRIGGER) | TỪ KHÓA NHẬN DIỆN |
| :--- | :--- | :--- |
| **ask_user_clarification** | Câu hỏi quá ngắn, mơ hồ, thiếu chủ ngữ hoặc không rõ ngữ cảnh. | "Tính đi", "Nó là ai", "Cái đó sao rồi" (khi không có history). |
| **web_search** | Cần thông tin **THỰC TẾ, THỜI GIAN THỰC** bên ngoài dự án. | "Tỷ giá", "Giá vàng", "Thời tiết", "Tin tức", "Thông số súng Glock ngoài đời", "mới nhất", "tra cứu". |
| **numerical_calculation** | Yêu cầu **TÍNH TOÁN CON SỐ**, thống kê, so sánh dữ liệu định lượng. | "Tính tổng", "Doanh thu", "Trung bình", "Đếm số lượng", "% tăng trưởng". |
| **unified** | User **ra lệnh chạy phân tích/trích xuất theo chương** (Unified: Bible + Timeline + Chunks + Relations). Bắt buộc có chapter_range [start, end] hoặc [ch, ch]. **KHÔNG** dùng cho "ghi nhớ quy tắc". | "Unified chương 1", "Phân tích dữ liệu chương 1 đến 10", "Chạy unified chương 5". |
| **chat_casual** | Chào hỏi, xã giao, **ghi nhớ quy tắc/ưu tiên**: "Hãy nhớ rằng...", "Từ giờ luật là...", "V hãy nghiêm khắc khi đánh giá..." → chọn **chat_casual** (không chọn unified). | "Hello", "Cảm ơn", "Hãy nhớ rằng luôn trả lời ngắn gọn", "Tôi muốn V nghiêm khắc khi đánh giá". |
| **query_Sql** | User **CHỈ MUỐN XEM/LIỆT KÊ** dữ liệu thô (không hỏi tự nhiên). Khi chọn query_Sql BẮT BUỘC điền **query_target**. **KHÔNG** chọn cho câu hỏi tự nhiên về quan hệ/timeline — những câu đó chọn search_context. | "Liệt kê chương", "Cho tôi xem luật", "Xuất timeline chương 2 dạng list". |
| **search_context** | **Intent thống nhất** cho mọi câu hỏi cần tra cứu/đọc nội dung dự án. Khi chọn search_context BẮT BUỘC điền **context_needs** (mảng, giá trị trong: "bible", "relation", "timeline", "chunk", "chapter") và **context_priority** (mảng cùng phần tử với context_needs nhưng **theo thứ tự ưu tiên** cho câu hỏi này: phần tử đầu = quan trọng nhất, dùng để tối ưu token). Ví dụ: "Trong chương 3 A làm gì và quan hệ B" -> context_needs: ["bible","relation","chapter"], context_priority: ["chapter","bible","relation"] (nội dung chương quan trọng nhất). | "A và B có quan hệ gì", "Trong chương 3 A làm gì và quan hệ B", "Sự kiện nào trước", "Tóm tắt chương 1", "nhân vật X". |
| **suggest_v7** | Câu hỏi **rõ ràng cần 2+ intent** (vd: "tóm tắt chương 1 rồi so sánh timeline"). Thao tác unified theo chương chỉ cần 1 bước unified. Dùng REFERENCE (bộ lọc nhanh) làm gợi ý; nếu đồng ý thì trả về suggest_v7. | "tóm tắt chương 1 rồi so sánh với timeline". |
| **check_chapter_logic** | User **hỏi về tính logic / mâu thuẫn / điểm vô lý / plot hole** của chương (soát lỗi logic theo timeline, bible, relation, crystallize, rule). **KHÔNG** chọn khi user chỉ hỏi nội dung, tóm tắt hay tra cứu thông thường — những câu đó dùng **search_context**. Khi chọn check_chapter_logic BẮT BUỘC điền **chapter_range** (chương cần soát). | "Chương 3 có điểm vô lý không", "Soát lỗi logic chương 5", "Mâu thuẫn trong chương 2", "Plot hole chương 1", "Kiểm tra logic chương 4". |

**BẢNG QUERY_TARGET (chỉ dùng khi intent = query_Sql):**
| query_target | Ý user | Ví dụ |
| :--- | :--- | :--- |
| chapters | Danh sách / thông tin chương | "Liệt kê chương", "Chương 1 tên gì", "Có bao nhiêu chương" |
| rules | Luật, quy tắc dự án | "Luật là gì", "Liệt kê quy tắc", "Rule nào đang dùng" |
| bible_entity | Entity Bible (nhân vật, địa điểm, khái niệm) | "Nhân vật A trong Bible", "Entity X có mô tả gì" |
| chunks | Đoạn văn đã chunk | "Chunks chương 2", "Đoạn văn đã tách chương 1" |
| timeline | Sự kiện, mốc thời gian (liệt kê/xem) | "Timeline chương 1", "Sự kiện chương 2", "Mốc thời gian" |
| relation | Chỉ khi user **liệt kê/xem dữ liệu** quan hệ (không hỏi tự nhiên). Câu hỏi "A và B có quan hệ gì" -> search_bible. | "Liệt kê quan hệ của A", "Xuất bảng relation" |
| summary | Tóm tắt (crystallize) | "Tóm tắt đã lưu", "Summary chương 3" |
| art | Nghệ thuật, style | "Nghệ thuật chương 1", "Style tác phẩm" |

### 3. HƯỚNG DẪN XỬ LÝ ĐẶC BIỆT (CRITICAL RULES)
1. **Quy tắc "Chương / đọc nội dung":** User nhắc "Chương X" hoặc "tóm tắt chương" / "xem nội dung chương" -> chọn **search_context** với **context_needs** chứa "chapter", điền chapter_range. **read_full_content KHÔNG bao giờ chọn** — chỉ dùng nội bộ khi search_context trả lời chưa đủ. Nếu user **ra lệnh chạy unified** theo chương (extract/ phân tích dữ liệu chương) với từ khóa rõ ràng (vd. "chạy unified", "run unified", "Unified chương X đến Y") -> `unified`, điền chapter_range.
2. **Quy tắc "Thực Tế":** Hỏi tỷ giá, tin tức, thời tiết -> BẮT BUỘC `web_search`.
3. **Quy tắc "Làm Rõ":** Câu quá ngắn/mơ hồ -> `ask_user_clarification`, điền `clarification_question`.
4. **Quy tắc "Tham chiếu chat cũ":** Tin nhắn chỉ tham chiếu lệnh trước (vd "làm cái đó", "ok làm đi") -> từ LỊCH SỬ CHAT: (1) **Phân định**: ĐÃ LÀM GÌ (bước/intent đã thực thi, kết quả đã có) vs CẦN LÀM GÌ (phần user muốn thực hiện tiếp hoặc câu hỏi còn lại). (2) Chỉ trả về intent và rewritten_query cho phần **CẦN LÀM**; không lặp lại bước đã làm. Lấy intent/rewritten_query từ tin nhắn user gần nhất có nội dung cụ thể.
5. **Quy tắc "Tham chiếu nội dung chat (crystallize)":** User nói đã bàn về X -> `search_context`, context_needs: ["bible"], rewritten_query: X.
6. **Quy tắc "Nhiều bước (suggest_v7)":** Câu hỏi rõ ràng cần 2+ intent hoặc 2+ thao tác -> `suggest_v7`.
7. **Quy tắc "unified vs chat_casual":** Chỉ khi user **ra lệnh chạy unified** theo chương với từ khóa rõ ràng như "unified", "chạy unified", "run unified" và có nói rõ chương/khoảng chương -> `unified`. "Hãy nhớ rằng", "từ giờ luật là", "V hãy nghiêm khắc khi..." -> `chat_casual`. Các câu hỏi tra cứu/thảo luận/tóm tắt (kể cả nói về nhiều chương) nhưng KHÔNG có lệnh rõ "chạy unified" -> **KHÔNG unified**, mà dùng `search_context`.
8. **Quy tắc "Tra cứu":** Tra cứu ngoài (tỷ giá, tin) -> `web_search`. Tra cứu nội dung dự án -> `search_context`.
9. **Quy tắc "query_Sql":** CHỈ khi user muốn XEM/LIỆT KÊ dữ liệu thô. Câu hỏi tự nhiên về quan hệ/timeline -> `search_context`. Điền **query_target** khi intent = query_Sql.
10. **Quy tắc "search_context — context_needs":** Luôn điền **context_needs** (mảng): hỏi quan hệ -> ["bible","relation"]; hỏi timeline/sự kiện -> ["timeline"] hoặc ["bible","timeline"]; hỏi chi tiết vụn (ai nói, câu nào) -> ["chunk"]; hỏi trong chương X kết hợp Bible -> ["bible","relation","chapter"] hoặc ["bible","chapter"]; chỉ tóm tắt chương -> ["chapter"]. Có thể kết hợp nhiều: ["bible","relation","timeline","chunk","chapter"] tùy câu hỏi.
11. **Quy tắc "check_chapter_logic vs search_context":** User hỏi **cụ thể về lỗi logic / mâu thuẫn / điểm vô lý / plot hole** của chương -> **check_chapter_logic**, điền chapter_range. User chỉ hỏi nội dung chương, tóm tắt, nhân vật làm gì, quan hệ... (tra cứu thông thường) -> **search_context**, không dùng check_chapter_logic.

### 4. LOGIC TRÍCH XUẤT CHAPTER RANGE
- TUYỆT ĐỐI KHÔNG tự ý bịa hoặc suy đoán chapter_range khi user KHÔNG nói rõ chương / khoảng chương / số chương / arc cụ thể.
- "Chương 1", "Chap 5" -> chapter_range_mode: "range", chapter_range: [1, 1] hoặc [5, 5]
- "Chương 1 đến 5" -> chapter_range_mode: "range", chapter_range: [1, 5]
- "3 chương đầu" -> chapter_range_mode: "first", chapter_range_count: 3
- "Chương mới nhất" -> chapter_range_mode: "latest", chapter_range_count: 1
- Khi user hỏi về MỘT ARC cụ thể (vd. "arc Tuổi thơ"), chỉ khi arc đó có danh sách chương rõ ràng trong phần dữ liệu, mới được đặt chapter_range = [min, max] theo các chương thuộc arc đó.
- TẤT CẢ câu hỏi KHÔNG LIÊN QUAN CHƯƠNG (chỉ hỏi nhân vật, quan hệ, phân tích chung, không nhắc số chương / khoảng chương / arc cụ thể) -> chapter_range: null, chapter_range_mode: null (KHÔNG được đặt [1,1] hay khoảng bất kỳ).

### 5. VÍ DỤ MINH HỌA (FEW-SHOT)
**Input:** "Tóm tắt nội dung chương 1 cho anh."
**Output:** {{ "intent": "search_context", "context_needs": ["chapter"], "context_priority": ["chapter"], "reason": "User tóm tắt chương 1. read_full_content không chọn; dùng search_context.", "chapter_range": [1, 1], "chapter_range_mode": "range", "rewritten_query": "Tóm tắt chương 1", "target_files": [], "target_bible_entities": [], "inferred_prefixes": [], "chapter_range_count": 5, "clarification_question": "", "update_summary": "", "query_target": "" }}

**Input:** "Tỷ giá USD/VND hôm nay bao nhiêu?"
**Output:** {{ "intent": "web_search", "context_needs": [], "reason": "Hỏi thông tin thời gian thực ngoài hệ thống.", "rewritten_query": "Tỷ giá USD VND hôm nay", "target_files": [], "target_bible_entities": [], "inferred_prefixes": [], "chapter_range": null, "chapter_range_mode": null, "chapter_range_count": 5, "clarification_question": "", "update_summary": "", "query_target": "" }}

**Input:** "Trong chương 3 nhân vật A làm gì và quan hệ với B thế nào?"
**Output:** {{ "intent": "search_context", "context_needs": ["bible", "relation", "chapter"], "context_priority": ["chapter", "bible", "relation"], "reason": "Một câu hỏi cần Bible, quan hệ và nội dung chương 3.", "chapter_range": [3, 3], "chapter_range_mode": "range", "rewritten_query": "Nhân vật A làm gì trong chương 3 và quan hệ với B", "target_files": [], "target_bible_entities": ["A", "B"], "inferred_prefixes": [], "chapter_range_count": 5, "clarification_question": "", "update_summary": "", "query_target": "" }}

**Input:** "A và B có quan hệ gì?" hoặc "Quan hệ giữa nhân vật X và Y?"
**Output:** {{ "intent": "search_context", "context_needs": ["bible", "relation"], "context_priority": ["bible", "relation"], "reason": "Hỏi quan hệ nhân vật.", "rewritten_query": "Quan hệ giữa A và B", "target_files": [], "target_bible_entities": ["A", "B"], "inferred_prefixes": [], "chapter_range": null, "chapter_range_mode": null, "chapter_range_count": 5, "clarification_question": "", "update_summary": "", "query_target": "" }}

**Input:** "Sự kiện nào diễn ra trước?"
**Output:** {{ "intent": "search_context", "context_needs": ["timeline"], "context_priority": ["timeline"], "reason": "Hỏi thứ tự sự kiện.", "rewritten_query": "Sự kiện nào diễn ra trước", "target_files": [], "target_bible_entities": [], "inferred_prefixes": [], "chapter_range": null, "chapter_range_mode": null, "chapter_range_count": 5, "clarification_question": "", "update_summary": "", "query_target": "" }}

**Input:** "Hùng cầm vũ khí gì?"
**Output:** {{ "intent": "search_context", "context_needs": ["chunk"], "context_priority": ["chunk"], "reason": "Hỏi chi tiết vụn trong văn bản.", "rewritten_query": "Hùng cầm vũ khí gì", "target_files": [], "target_bible_entities": [], "inferred_prefixes": [], "chapter_range": null, "chapter_range_mode": null, "chapter_range_count": 5, "clarification_question": "", "update_summary": "", "query_target": "" }}

**Input:** "Tóm tắt chương 1 rồi so sánh với timeline chương 2."
**Output:** {{ "intent": "suggest_v7", "context_needs": [], "reason": "User yêu cầu hai việc: tóm tắt và so sánh timeline. Cần nhiều bước.", "rewritten_query": "Tóm tắt chương 1 rồi so sánh với timeline chương 2", "target_files": [], "target_bible_entities": [], "inferred_prefixes": [], "chapter_range": null, "chapter_range_mode": null, "chapter_range_count": 5, "clarification_question": "", "update_summary": "" }}

**Input:** "Chạy unified chương 1 đến 10"
**Output:** {{ "intent": "unified", "context_needs": [], "reason": "User yêu cầu chạy phân tích unified theo chương.", "rewritten_query": "Unified chương 1 đến 10", "target_files": [], "target_bible_entities": [], "inferred_prefixes": [], "chapter_range": [1, 10], "chapter_range_mode": "range", "chapter_range_count": 5, "clarification_question": "", "update_summary": "", "data_operation_type": "extract", "data_operation_target": "unified", "query_target": "" }}

**Input:** "Phân tích dữ liệu chương 5"
**Output:** {{ "intent": "unified", "context_needs": [], "reason": "User yêu cầu unified một chương.", "rewritten_query": "Unified chương 5", "target_files": [], "target_bible_entities": [], "inferred_prefixes": [], "chapter_range": [5, 5], "chapter_range_mode": "range", "chapter_range_count": 5, "clarification_question": "", "update_summary": "", "data_operation_type": "extract", "data_operation_target": "unified", "query_target": "" }}

### 6. INPUT CỦA USER
"{user_prompt}"

### 7. OUTPUT (JSON ONLY) — Trả về đúng format sau, đủ các key:
{{
    "intent": "ask_user_clarification" | "web_search" | "numerical_calculation" | "unified" | "query_Sql" | "search_context" | "suggest_v7" | "check_chapter_logic" | "chat_casual",
    "context_needs": [] hoặc ["bible"] | ["relation"] | ["timeline"] | ["chunk"] | ["chapter"] hoặc kết hợp (BẮT BUỘC khi intent = search_context),
    "context_priority": [] hoặc mảng cùng phần tử với context_needs theo thứ tự ưu tiên (phần tử đầu = quan trọng nhất; dùng để tối ưu token; BẮT BUỘC khi intent = search_context),
    "target_files": [],
    "target_bible_entities": [],
    "target_chunk_keywords": [],
    "target_relation_entities": [],
    "target_timeline_keywords": [],
    "inferred_prefixes": [],
    "reason": "Lý do ngắn gọn bằng tiếng Việt",
    "rewritten_query": "Viết lại câu hỏi cho search",
    "chapter_range": null hoặc [start, end],
    "chapter_range_mode": null hoặc "first" | "latest" | "range",
    "chapter_range_count": 5,
    "clarification_question": "" hoặc "Câu hỏi gợi ý (khi intent ask_user_clarification)",
    "update_summary": "",
    "data_operation_type": "" hoặc "extract" (khi intent unified),
    "data_operation_target": "" hoặc "unified",
    "query_target": "" hoặc "chapters" | "rules" | "bible_entity" | "chunks" | "timeline" | "relation" | "summary" | "art" (BẮT BUỘC khi intent = query_Sql)
}}
"""

        messages = [
            {"role": "system", "content": "Bạn là AI Router thông minh. Chỉ trả về JSON."},
            {"role": "user", "content": router_prompt}
        ]
        try:
            response = AIService.call_openrouter(
                messages=messages,
                model=_get_default_tool_model(),
                temperature=0.1,
                max_tokens=500,
                response_format={"type": "json_object"}
            )
            content = response.choices[0].message.content
            content = AIService.clean_json_text(content)
            result = json.loads(content)
            result.setdefault("target_files", [])
            result.setdefault("target_bible_entities", [])
            result.setdefault("target_chunk_keywords", [])
            result.setdefault("target_relation_entities", [])
            result.setdefault("target_timeline_keywords", [])
            result.setdefault("inferred_prefixes", [])
            result.setdefault("rewritten_query", user_prompt)
            result.setdefault("chapter_range", None)
            result.setdefault("chapter_range_mode", None)
            result.setdefault("chapter_range_count", 5)
            result.setdefault("clarification_question", "")
            result.setdefault("update_summary", "")
            result.setdefault("data_operation_type", "")
            result.setdefault("data_operation_target", "")
            result.setdefault("query_target", "")
            result.setdefault("context_needs", [])
            result.setdefault("context_priority", [])
            # Guardrail unified: chỉ giữ intent=unified khi user RA LỆNH rõ ràng và có chapter_range hợp lệ.
            intent_raw = (result.get("intent") or "chat_casual").strip().lower()
            user_low = (user_prompt or "").lower()
            if intent_raw == "unified":
                trigger_phrases = ("unified", "chạy unified", "run unified")
                has_trigger = any(p in user_low for p in trigger_phrases)
                ch = result.get("chapter_range")
                has_valid_range = isinstance(ch, (list, tuple)) and len(ch) >= 1
                if (not has_trigger) or (not has_valid_range):
                    # Hạ unified về search_context cho các câu Q&A/tra cứu bình thường.
                    result["intent"] = "search_context"
                    if not result.get("context_needs"):
                        result["context_needs"] = ["bible", "relation", "chapter", "timeline", "chunk"]
            # Legacy: update_data -> unified (chỉ còn nhánh unified cho thao tác theo chương).
            if result.get("intent") == "update_data":
                result["intent"] = "unified"
            # Chuẩn hóa intent cũ -> search_context
            legacy_search = ("read_full_content", "search_bible", "mixed_context", "manage_timeline", "search_chunks")
            if result.get("intent") in legacy_search:
                old = result["intent"]
                result["intent"] = "search_context"
                if not result.get("context_needs"):
                    if old == "read_full_content":
                        result["context_needs"] = ["chapter"]
                    elif old == "manage_timeline":
                        result["context_needs"] = ["timeline"]
                    elif old == "search_chunks":
                        result["context_needs"] = ["chunk"]
                    elif old == "search_bible":
                        result["context_needs"] = ["bible", "relation"]
                    else:
                        result["context_needs"] = ["bible", "relation", "chapter", "timeline", "chunk"]
            # Schema: chuẩn hóa context_needs và context_priority
            if result.get("intent") == "search_context":
                needs = normalize_context_needs(result.get("context_needs"))
                if not needs:
                    needs = infer_default_context_needs(result)
                result["context_needs"] = needs
                result["context_priority"] = normalize_context_priority(result.get("context_priority"), needs)
            if not isinstance(result.get("inferred_prefixes"), list):
                result["inferred_prefixes"] = []
            valid_keys = Config.get_valid_prefix_keys()
            if valid_keys:
                result["inferred_prefixes"] = [
                    p for p in result["inferred_prefixes"]
                    if p and str(p).strip().upper().replace(" ", "_") in valid_keys
                ]
            return result
        except Exception as e:
            print(f"Router error: {e}")
            return {
                "intent": "chat_casual",
                "context_needs": [], "context_priority": [],
                "target_files": [], "target_bible_entities": [], "inferred_prefixes": [],
                "reason": f"Router error: {e}", "rewritten_query": user_prompt,
                "chapter_range": None, "chapter_range_mode": None, "chapter_range_count": 5,
                "clarification_question": "", "update_summary": "",
                "data_operation_type": "", "data_operation_target": "", "query_target": "",
            }

    @staticmethod
    def get_plan_v7(user_prompt: str, chat_history_text: str, project_id: str = None) -> Dict:
        """V7 Agentic Planner: Trả về plan (mảng bước) thay vì single intent."""
        rules_context = ""
        bible_index = ""
        prefix_setup_str = ""
        if project_id:
            rules_context = get_mandatory_rules(project_id)
            bible_index = get_bible_index(project_id, max_tokens=2000)
        try:
            prefix_setup = Config.get_prefix_setup()
            prefix_setup_str = "\n".join(
                f"- [{p.get('prefix_key', '')}]: {p.get('description', '')}" for p in (prefix_setup or [])
            ) if prefix_setup else "(Chưa cấu hình Bible Prefix.)"
        except Exception:
            prefix_setup_str = "(Chưa cấu hình Bible Prefix.)"
        chat_history_capped = cap_chat_history_to_tokens(chat_history_text or "")
        chapter_list_str = get_chapter_list_for_router(project_id) if project_id else "(Trống)"
        planner_prompt = f"""Bạn là V7 Planner. Nhiệm vụ: phân tích câu user và đưa ra KẾ HOẠCH (mảng bước) thực thi.

DỮ LIỆU: QUY TẮC={rules_context[:1500]} | PREFIX={prefix_setup_str[:800]} | BIBLE INDEX={bible_index[:2000] if bible_index else "(Trống)"} | DANH SÁCH CHƯƠNG (số - tên)={chapter_list_str} | LỊCH SỬ={chat_history_capped}

INPUT USER: "{user_prompt}"

QUY TẮC:
- **Tham chiếu chat cũ — phân định ĐÃ LÀM / CẦN LÀM:** Khi user tham chiếu lệnh trước (vd "làm đi", "cái đó", "tiếp đi"): (1) Từ LỊCH SỬ xác định **ĐÃ LÀM GÌ** (các bước/intent đã thực thi, kết quả model đã trả lời). (2) Xác định **CẦN LÀM GÌ** (phần còn lại user muốn, hoặc câu hỏi mới). (3) Chỉ lên plan cho phần **CẦN LÀM**; không thêm bước lặp lại việc đã làm. (4) Mỗi bước trong plan: **query_refined** = câu hỏi/nội dung **chỉ dành cho bước đó** (phần cần làm của bước đó), không gộp cả "đã làm".
- **search_context (intent thống nhất):** Mọi câu hỏi cần tra cứu/đọc (lore, nhân vật, quan hệ, timeline, chunk, tóm tắt chương) -> ĐÚNG MỘT bước intent `search_context`. BẮT BUỘC điền **context_needs** trong args: mảng ["bible"] | ["relation"] | ["timeline"] | ["chunk"] | ["chapter"] hoặc kết hợp. read_full_content KHÔNG dùng; chỉ fallback nội bộ khi trả lời chưa đủ.
- **multi_chapter_analysis (V7, khoảng chương lớn):** Khi user yêu cầu phân tích/tổng hợp trên một khoảng chương RỘNG (vd "chương 1 đến 30", "từ chương 5-40") và câu hỏi phức tạp (so sánh, thống kê, logic hole...), dùng **một bước** intent `multi_chapter_analysis` với args.chapter_range = [start, end]. Executor sẽ tự chia khoảng này thành nhiều đoạn nhỏ (1–10, 11–20, 21–30, ...) để xử lý nội bộ và gom kết quả lại. KHÔNG cần thêm nhiều bước `search_context` riêng lẻ cho từng đoạn.
- **Nhiều bước (plan 2+ step):** Chỉ khi user nói RÕ nhiều việc (vd "tóm tắt chương 1 rồi so sánh với timeline") -> tách nhiều bước, dependency khi cần.
- unified chỉ khi ra lệnh chạy unified theo chương; args có chapter_range. query_Sql chỉ khi XEM/LIỆT KÊ dữ liệu thô; args có query_target. check_chapter_logic khi user hỏi về lỗi logic/mâu thuẫn/điểm vô lý của chương — điền chapter_range. dependency: null cho unified, query_Sql, web_search, ask_user_clarification, chat_casual, check_chapter_logic. verification_required: true nếu plan có numerical_calculation, search_context, query_Sql, check_chapter_logic, multi_chapter_analysis.

Trả về ĐÚNG MỘT JSON:
- **analysis**: Mô tả ngắn; nếu dùng LỊCH SỬ thì ghi rõ: "Đã làm: ...; Cần làm: ..." để plan chỉ chạy đúng bước còn lại.
- **plan**: Chỉ gồm các bước **CẦN LÀM** (không lặp bước đã làm). Mỗi bước có args.query_refined = nội dung chỉ cho bước đó.

{{ "analysis": "...", "plan": [ {{ "step_id": 1, "intent": "...", "args": {{ "query_refined": "...", "context_needs": [], "target_files": [], "target_bible_entities": [], "chapter_range": null, "chapter_range_mode": null, "chapter_range_count": 5, "data_operation_type": "", "data_operation_target": "", "query_target": "" }}, "dependency": null }} ], "verification_required": true }}
Chỉ trả về JSON."""

        try:
            response = AIService.call_openrouter(
                messages=[
                    {"role": "system", "content": "Bạn là V7 Planner. Chỉ trả về JSON với analysis, plan, verification_required."},
                    {"role": "user", "content": planner_prompt}
                ],
                model=_get_default_tool_model(),
                temperature=0.1,
                max_tokens=800,
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content
            content = AIService.clean_json_text(content)
            data = json.loads(content)
        except Exception as e:
            print(f"Planner V7 error: {e}")
            single = SmartAIRouter.ai_router_pro_v2(user_prompt, chat_history_text, project_id)
            return SmartAIRouter._single_intent_to_plan(single, user_prompt)
        plan = data.get("plan")
        if not plan or not isinstance(plan, list):
            single = SmartAIRouter.ai_router_pro_v2(user_prompt, chat_history_text, project_id)
            return SmartAIRouter._single_intent_to_plan(single, user_prompt)
        analysis = data.get("analysis", "")
        verification_required = bool(data.get("verification_required", False))
        valid_intents = {"numerical_calculation", "search_context", "web_search", "ask_user_clarification", "unified", "query_Sql", "check_chapter_logic", "chat_casual", "multi_chapter_analysis"}
        normalized_plan = []
        for i, s in enumerate(plan):
            if not isinstance(s, dict):
                continue
            intent = (s.get("intent") or "chat_casual").strip().lower()
            if intent == "update_data":
                intent = "unified"
            # Planner không nên trả về intent "suggest_v7" (đây là intent của router V6 dùng để GỢI Ý xài V7).
            # Nếu vẫn gặp intent này thì coi như một yêu cầu phân tích nhiều chương.
            if intent == "suggest_v7":
                intent = "multi_chapter_analysis"
            # Nếu Planner trả về intent "suggest_v7" thì coi như một yêu cầu phân tích nhiều chương.
            if intent == "suggest_v7":
                intent = "multi_chapter_analysis"
            args = s.get("args") or {}
            if not isinstance(args, dict):
                args = {}
            if intent not in valid_intents:
                if intent in ("extract_bible", "extract_relation", "extract_timeline", "extract_chunking"):
                    intent = "unified"
                    args = dict(args)
                    if not args.get("data_operation_target"):
                        args["data_operation_target"] = "unified"
                    if not args.get("data_operation_type"):
                        args["data_operation_type"] = "extract"
                elif intent in ("read_full_content", "search_bible", "mixed_context", "manage_timeline", "search_chunks"):
                    intent = "search_context"
                    args = dict(args)
                    if not args.get("context_needs"):
                        args["context_needs"] = ["bible", "relation", "chapter", "timeline", "chunk"]
                else:
                    intent = "chat_casual"
            step_id = int(s.get("step_id", i + 1))
            dependency = s.get("dependency")
            normalized_plan.append({
                "step_id": step_id,
                "intent": intent,
                "args": {
                    "query_refined": args.get("query_refined") or args.get("rewritten_query") or user_prompt,
                    "context_needs": args.get("context_needs") if isinstance(args.get("context_needs"), list) else [],
                    "context_priority": args.get("context_priority") if isinstance(args.get("context_priority"), list) else [],
                    "target_files": args.get("target_files") if isinstance(args.get("target_files"), list) else [],
                    "target_bible_entities": args.get("target_bible_entities") if isinstance(args.get("target_bible_entities"), list) else [],
                    "chapter_range": args.get("chapter_range"),
                    "chapter_range_mode": args.get("chapter_range_mode"),
                    "chapter_range_count": args.get("chapter_range_count", 5),
                    "inferred_prefixes": args.get("inferred_prefixes") if isinstance(args.get("inferred_prefixes"), list) else [],
                    "clarification_question": args.get("clarification_question") or "",
                    "update_summary": args.get("update_summary") or "",
                    "data_operation_type": args.get("data_operation_type") or "",
                    "data_operation_target": args.get("data_operation_target") or "",
                    "query_target": args.get("query_target") or "",
                },
                "dependency": dependency,
            })
        if not normalized_plan:
            single = SmartAIRouter.ai_router_pro_v2(user_prompt, chat_history_text, project_id)
            return SmartAIRouter._single_intent_to_plan(single, user_prompt)
        # Auto-upgrade search_context trên khoảng chương rất rộng thành multi_chapter_analysis để V7 executor xử lý nhiều sub-range nội bộ.
        for s in normalized_plan:
            if s.get("intent") == "search_context":
                args_s = s.get("args") or {}
                cr = args_s.get("chapter_range")
                if isinstance(cr, (list, tuple)) and len(cr) >= 2:
                    try:
                        start_cr, end_cr = int(cr[0]), int(cr[1])
                        if end_cr - start_cr + 1 >= 20:
                            s["intent"] = "multi_chapter_analysis"
                    except (ValueError, TypeError):
                        pass
        intents_need_verify = {"numerical_calculation", "search_context", "query_Sql", "check_chapter_logic", "multi_chapter_analysis"}
        if any(s.get("intent") in intents_need_verify for s in normalized_plan):
            verification_required = True
        return {"analysis": analysis, "plan": normalized_plan, "verification_required": verification_required}

    @staticmethod
    def get_plan_v7_light(
        user_prompt: str,
        chat_history_text: str,
        project_id: str = None,
        intent_from_step1: str = "",
    ) -> Dict:
        """V7 Planner theo mô hình 3 bước: đã có intent từ bước 1; chỉ sinh plan tối đa 3 bước, prompt ngắn để nhanh."""
        rules_context = ""
        bible_index = ""
        if project_id:
            rules_context = get_mandatory_rules(project_id)
            bible_index = get_bible_index(project_id, max_tokens=1200)
        chat_history_capped = cap_chat_history_to_tokens(chat_history_text or "")
        chapter_list_str = get_chapter_list_for_router(project_id) if project_id else "(Trống)"
        planner_prompt = f"""### VAI TRÒ
Bạn là **Planner V7** cho hệ thống V7-Universal. Nhiệm vụ: từ câu hỏi của User, đưa ra **KẾ HOẠCH tối đa 3 bước**, mỗi bước 1 intent rõ ràng để Executor V7 có thể:
- Lấy đúng context cần thiết (theo chương, Bible, timeline, relation, chunk...)
- Thực hiện các thao tác phân tích / thống kê / unified / web_search tương ứng.

### 1. DỮ LIỆU ĐẦU VÀO (CHỈ DÙNG ĐỂ QUYẾT ĐỊNH PLAN, KHÔNG PHẢI NỘI DUNG CHÍNH XÁC)
- QUY TẮC DỰ ÁN (rút gọn): {rules_context[:800]}
- DANH SÁCH ENTITY (Bible - index rút gọn): {bible_index[:1000] if bible_index else "(Trống)"}
- DANH SÁCH CHƯƠNG (số - tên): {chapter_list_str}
- LỊCH SỬ CHAT (rút gọn): {chat_history_capped[:600]}
- GỢI Ý TỪ BƯỚC 1 (intent_only): {intent_from_step1 or "search_context"} — chỉ dùng tham khảo, bạn được quyền chọn intent khác nếu hợp lý hơn.

### 2. DANH SÁCH INTENT HỖ TRỢ
- **search_context**: Mọi câu hỏi cần tra cứu/nội dung dự án (Bible, relation, timeline, chunk, chapter). BẮT BUỘC có `context_needs` (mảng giá trị trong: "bible", "relation", "timeline", "chunk", "chapter").
- **multi_chapter_analysis**: Phân tích **một khoảng chương lớn** (ví dụ 1–30) theo yêu cầu cụ thể (tóm tắt, so sánh, tìm plot hole...). Dùng khi user nói rõ khoảng chương (vd. "chương 1 đến 30", "từ chương 5 tới 15") và muốn phân tích tổng hợp trên khoảng đó.
- **check_chapter_logic**: Soát lỗi logic / mâu thuẫn / plot hole của **chương hoặc khoảng chương đã nêu rõ**. BẮT BUỘC có `chapter_range`.
- **unified**: User ra lệnh **chạy unified theo chương** (extract / phân tích dữ liệu chương) với từ khóa rõ ràng ("unified", "chạy unified", "run unified") và có `chapter_range`.
- **numerical_calculation**: Tính toán số liệu, thống kê, so sánh định lượng.
- **query_Sql**: User chỉ muốn XEM/LIỆT KÊ dữ liệu thô (bảng, danh sách...), không phải câu hỏi tự nhiên.
- **web_search**: Thông tin thời gian thực / ngoài dự án (tỷ giá, tin tức...).
- **chat_casual**: Chào hỏi, trò chuyện, bàn luận chung **không cần tra cứu dữ liệu dự án**.
- **ask_user_clarification**: CHỈ dùng khi câu hỏi **quá mơ hồ**, không thể suy ra chương / khoảng chương / entity / mục tiêu cụ thể nào, và cả lịch sử chat cũng không đủ để đoán.

### 3. QUY TẮC QUAN TRỌNG KHI LẬP KẾ HOẠCH
1. **Khi user đã nói rõ chương hoặc khoảng chương** (vd. "chương 1", "chương 1 đến 30", "chương 5-10"):
   - Nếu họ hỏi **nội dung / tóm tắt / phân tích thông thường** -> dùng `search_context` với:
     - `chapter_range` tương ứng với chương/khoảng chương user nêu.
     - `context_needs` chứa ít nhất "chapter", có thể thêm "bible", "relation", "timeline" tùy câu hỏi.
   - Nếu họ hỏi **về điểm vô lý / mâu thuẫn / plot hole / logic** -> ưu tiên `check_chapter_logic` hoặc `multi_chapter_analysis` (khi khoảng chương lớn), luôn có `chapter_range`.

2. **Khi user nhắc tới khoảng chương RẤT RỘNG** (vd. 1–20, 1–30, 10–50) và yêu cầu phân tích/tổng hợp/phát hiện logic hole trên khoảng đó:
   - Ưu tiên tạo 1 step với intent `multi_chapter_analysis`, `args.chapter_range = [start, end]`.
   - Không dùng `ask_user_clarification` trong trường hợp này nếu user đã ghi rõ khoảng chương và mục tiêu (tóm tắt, so sánh, tìm plot hole...).

3. **Hạn chế tối đa `ask_user_clarification`**:
   - CHỈ chọn `ask_user_clarification` khi:
     - Câu hỏi cực kỳ ngắn hoặc chung chung (ví dụ: "Kiểm tra giúp", "Sửa lại đi") VÀ
     - Không nhắc đến chương, khoảng chương, arc, nhân vật, hệ thống, hoặc mục tiêu rõ ràng (tóm tắt / so sánh / tìm lỗi logic...), VÀ
     - Lịch sử chat không chứa câu hỏi cụ thể ngay trước đó để tham chiếu.
   - Nếu user đã nói rõ **chương, entity, hệ thống, hoặc mục tiêu** thì PHẢI chọn intent cụ thể (`search_context`, `multi_chapter_analysis`, `check_chapter_logic`, `unified`, v.v.), KHÔNG dùng `ask_user_clarification`.

4. **Khi một câu hỏi có thể chia thành nhiều thao tác rõ ràng** (ví dụ "tóm tắt chương 1 rồi so sánh với timeline chương 2", "so sánh sức mạnh Cường ở chương 5 và chương 20"):
   - Bạn được phép tạo tối đa 3 bước, mỗi bước 1 intent, ví dụ:
     - B1: `search_context` hoặc `multi_chapter_analysis` cho chương/khoảng chương đầu.
     - B2: `search_context` hoặc `multi_chapter_analysis` cho chương/khoảng chương sau.
     - B3: `numerical_calculation` hoặc `search_context` để so sánh/tổng hợp nếu cần.

### 4. ĐỊNH DẠNG OUTPUT
INPUT CỦA USER: "{user_prompt}"

Hãy trả về ĐÚNG MỘT JSON với format:
{{ 
  "analysis": "1 câu tóm tắt ngắn gọn về kế hoạch xử lý câu hỏi này.",
  "plan": [
    {{
      "step_id": 1,
      "intent": "search_context | multi_chapter_analysis | check_chapter_logic | unified | numerical_calculation | query_Sql | web_search | chat_casual | ask_user_clarification",
      "args": {{
        "query_refined": "...", 
        "context_needs": [], 
        "chapter_range": null, 
        "chapter_range_mode": null,
        "chapter_range_count": 5,
        "data_operation_type": "", 
        "data_operation_target": "", 
        "query_target": "",
        "target_files": [],
        "target_bible_entities": [],
        "clarification_question": ""
      }},
      "dependency": null
    }}
  ],
  "verification_required": true
}}

LƯU Ý:
- Tối đa 3 bước trong `plan`.
- Nếu chỉ cần 1 bước thì vẫn trả về mảng `plan` với 1 phần tử.
- **Không** trả lời tự nhiên, **chỉ** trả về JSON đúng format trên."""

        try:
            response = AIService.call_openrouter(
                messages=[
                    {"role": "system", "content": "Planner. Chỉ trả về JSON. Plan tối đa 3 bước."},
                    {"role": "user", "content": planner_prompt},
                ],
                model=_get_default_tool_model(),
                temperature=0.1,
                max_tokens=500,
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content
            content = AIService.clean_json_text(content)
            data = json.loads(content)
        except Exception as e:
            print(f"Planner V7 light error: {e}")
            single = SmartAIRouter.ai_router_pro_v2(user_prompt, chat_history_text, project_id)
            return SmartAIRouter._single_intent_to_plan(single, user_prompt)
        plan = data.get("plan")
        if not plan or not isinstance(plan, list):
            single = SmartAIRouter.ai_router_pro_v2(user_prompt, chat_history_text, project_id)
            return SmartAIRouter._single_intent_to_plan(single, user_prompt)
        plan = plan[:3]
        analysis = data.get("analysis", "")
        verification_required = bool(data.get("verification_required", False))
        valid_intents = {"numerical_calculation", "search_context", "web_search", "ask_user_clarification", "unified", "query_Sql", "check_chapter_logic", "chat_casual", "multi_chapter_analysis"}
        normalized_plan = []
        for i, s in enumerate(plan):
            if not isinstance(s, dict):
                continue
            intent = (s.get("intent") or "chat_casual").strip().lower()
            if intent == "update_data":
                intent = "unified"
            args = s.get("args") or {}
            if not isinstance(args, dict):
                args = {}
            if intent not in valid_intents:
                intent = "search_context"
                args = dict(args)
                if not args.get("context_needs"):
                    args["context_needs"] = ["bible", "relation", "chapter", "timeline", "chunk"]
            step_id = int(s.get("step_id", i + 1))
            normalized_plan.append({
                "step_id": step_id,
                "intent": intent,
                "args": {
                    "query_refined": args.get("query_refined") or args.get("rewritten_query") or user_prompt,
                    "context_needs": args.get("context_needs") if isinstance(args.get("context_needs"), list) else [],
                    "context_priority": args.get("context_priority") if isinstance(args.get("context_priority"), list) else [],
                    "target_files": args.get("target_files") if isinstance(args.get("target_files"), list) else [],
                    "target_bible_entities": args.get("target_bible_entities") if isinstance(args.get("target_bible_entities"), list) else [],
                    "chapter_range": args.get("chapter_range"),
                    "chapter_range_mode": args.get("chapter_range_mode"),
                    "chapter_range_count": args.get("chapter_range_count", 5),
                    "inferred_prefixes": args.get("inferred_prefixes") if isinstance(args.get("inferred_prefixes"), list) else [],
                    "clarification_question": args.get("clarification_question") or "",
                    "update_summary": args.get("update_summary") or "",
                    "data_operation_type": args.get("data_operation_type") or "",
                    "data_operation_target": args.get("data_operation_target") or "",
                    "query_target": args.get("query_target") or "",
                },
                "dependency": s.get("dependency"),
            })
        if not normalized_plan:
            single = SmartAIRouter.ai_router_pro_v2(user_prompt, chat_history_text, project_id)
            return SmartAIRouter._single_intent_to_plan(single, user_prompt)
        # Áp dụng multi_chapter_analysis cho search_context với khoảng chương rất rộng trong bản light.
        for s in normalized_plan:
            if s.get("intent") == "search_context":
                args_s = s.get("args") or {}
                cr = args_s.get("chapter_range")
                if isinstance(cr, (list, tuple)) and len(cr) >= 2:
                    try:
                        start_cr, end_cr = int(cr[0]), int(cr[1])
                        if end_cr - start_cr + 1 >= 20:
                            s["intent"] = "multi_chapter_analysis"
                    except (ValueError, TypeError):
                        pass
        if any(s.get("intent") in {"numerical_calculation", "search_context", "query_Sql", "check_chapter_logic", "multi_chapter_analysis"} for s in normalized_plan):
            verification_required = True
        return {"analysis": analysis, "plan": normalized_plan, "verification_required": verification_required}

    @staticmethod
    def _single_intent_to_plan(single_router_result: Dict, user_prompt: str) -> Dict:
        """Chuyển kết quả router single-intent thành plan 1 bước (tương thích V7)."""
        intent = (single_router_result.get("intent") or "chat_casual").strip()
        # Trong Planner V7, intent "suggest_v7" (gợi ý dùng V7) không phải là bước thực thi.
        # Nếu fallback từ router trả về intent này thì coi như một yêu cầu phân tích nhiều chương.
        if intent.lower() == "suggest_v7":
            intent = "multi_chapter_analysis"
        return {
            "analysis": single_router_result.get("reason", ""),
            "plan": [{
                "step_id": 1,
                "intent": intent,
                "args": {
                    "query_refined": single_router_result.get("rewritten_query") or user_prompt,
                    "context_needs": single_router_result.get("context_needs") or [],
                    "context_priority": single_router_result.get("context_priority") or [],
                    "target_files": single_router_result.get("target_files") or [],
                    "target_bible_entities": single_router_result.get("target_bible_entities") or [],
                    "chapter_range": single_router_result.get("chapter_range"),
                    "chapter_range_mode": single_router_result.get("chapter_range_mode"),
                    "chapter_range_count": single_router_result.get("chapter_range_count", 5),
                    "inferred_prefixes": single_router_result.get("inferred_prefixes") or [],
                    "clarification_question": single_router_result.get("clarification_question") or "",
                    "update_summary": single_router_result.get("update_summary") or "",
                    "data_operation_type": single_router_result.get("data_operation_type") or "",
                    "data_operation_target": single_router_result.get("data_operation_target") or "",
                    "query_target": single_router_result.get("query_target") or "",
                },
                "dependency": None,
            }],
            "verification_required": intent in ("numerical_calculation", "search_context", "query_Sql", "check_chapter_logic", "multi_chapter_analysis"),
        }
