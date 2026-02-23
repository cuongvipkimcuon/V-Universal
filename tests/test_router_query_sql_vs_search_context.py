# tests/test_router_query_sql_vs_search_context.py
"""
Unit test: Router phân biệt rõ intent query_Sql (xem/liệt kê dữ liệu thô) vs search_context (hỏi tự nhiên).
Dùng mock LLM trả về intent cố định để kiểm tra pipeline parse + chuẩn hóa.

Chạy trong môi trường có cài đủ dependency (streamlit, ...):
  python -m pytest tests/test_router_query_sql_vs_search_context.py -v
  hoặc: python -m unittest tests.test_router_query_sql_vs_search_context -v
"""
import json
import unittest
from unittest.mock import patch, MagicMock


# Sample prompts: kỳ vọng query_Sql khi user muốn XEM/LIỆT KÊ dữ liệu thô
QUERY_SQL_SAMPLES = [
    "Liệt kê danh sách chương",
    "Cho tôi xem tất cả các chương",
    "Hiển thị timeline dạng bảng",
    "Xem danh sách luật trong dự án",
    "List tất cả nhân vật trong bible",
]

# Sample prompts: kỳ vọng search_context khi user hỏi tự nhiên (tra cứu nội dung)
SEARCH_CONTEXT_SAMPLES = [
    "Nhân vật A làm gì trong chương 1?",
    "Quan hệ giữa B và C là gì?",
    "Chương 3 kể về điều gì?",
    "Timeline của sự kiện X diễn ra thế nào?",
    "Tóm tắt nội dung chương 2",
]


class TestRouterQuerySqlVsSearchContext(unittest.TestCase):
    """Đảm bảo router phân biệt query_Sql (xem/liệt kê dữ liệu thô) và search_context (hỏi tự nhiên)."""

    @patch("ai.router.AIService.call_openrouter")
    def test_intent_query_sql_when_llm_returns_query_sql(self, mock_call):
        """Khi LLM trả về intent query_Sql cho câu 'liệt kê chương', pipeline phải giữ intent query_Sql."""
        from ai.router import SmartAIRouter

        mock_call.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content=json.dumps({
                "intent": "query_Sql",
                "rewritten_query": "Liệt kê danh sách chương",
                "clarification_question": "",
                "relevant_rules": "",
            })))]
        )
        result = SmartAIRouter.intent_only_classifier(
            "Liệt kê danh sách chương",
            chat_history_text="",
            project_id=None,
        )
        self.assertEqual(result.get("intent"), "query_Sql", "Câu 'liệt kê chương' phải được phân loại query_Sql")

    @patch("ai.router.AIService.call_openrouter")
    def test_intent_search_context_when_llm_returns_search_context(self, mock_call):
        """Khi LLM trả về intent search_context cho câu hỏi tự nhiên, pipeline phải giữ search_context."""
        from ai.router import SmartAIRouter

        mock_call.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content=json.dumps({
                "intent": "search_context",
                "rewritten_query": "Nhân vật A làm gì trong chương 1",
                "clarification_question": "",
                "relevant_rules": "",
            })))]
        )
        result = SmartAIRouter.intent_only_classifier(
            "Nhân vật A làm gì trong chương 1?",
            chat_history_text="",
            project_id=None,
        )
        self.assertEqual(result.get("intent"), "search_context", "Câu hỏi tự nhiên phải được phân loại search_context")

    @patch("ai.router.AIService.call_openrouter")
    def test_query_sql_samples_expect_query_sql(self, mock_call):
        """Với các câu mẫu 'xem/liệt kê dữ liệu thô', khi LLM trả query_Sql thì kết quả phải là query_Sql."""
        from ai.router import SmartAIRouter

        for prompt in QUERY_SQL_SAMPLES:
            mock_call.return_value = MagicMock(
                choices=[MagicMock(message=MagicMock(content=json.dumps({
                    "intent": "query_Sql",
                    "rewritten_query": prompt,
                    "clarification_question": "",
                    "relevant_rules": "",
                })))]
            )
            result = SmartAIRouter.intent_only_classifier(prompt, "", project_id=None)
            self.assertEqual(result.get("intent"), "query_Sql", "Mẫu '%s' kỳ vọng query_Sql" % prompt[:40])

    @patch("ai.router.AIService.call_openrouter")
    def test_search_context_samples_expect_search_context(self, mock_call):
        """Với các câu mẫu 'hỏi tự nhiên', khi LLM trả search_context thì kết quả phải là search_context."""
        from ai.router import SmartAIRouter

        for prompt in SEARCH_CONTEXT_SAMPLES:
            mock_call.return_value = MagicMock(
                choices=[MagicMock(message=MagicMock(content=json.dumps({
                    "intent": "search_context",
                    "rewritten_query": prompt,
                    "clarification_question": "",
                    "relevant_rules": "",
                })))]
            )
            result = SmartAIRouter.intent_only_classifier(prompt, "", project_id=None)
            self.assertEqual(result.get("intent"), "search_context", "Mẫu '%s' kỳ vọng search_context" % prompt[:40])

    def test_intent_handler_map_has_query_sql_and_search_context(self):
        """INTENT_HANDLER_MAP phải có query_Sql và search_context, và map tới handler phù hợp."""
        from ai.router import INTENT_HANDLER_MAP

        self.assertIn("query_Sql", INTENT_HANDLER_MAP)
        self.assertIn("search_context", INTENT_HANDLER_MAP)
        self.assertEqual(INTENT_HANDLER_MAP["query_Sql"], "llm_with_context")
        self.assertEqual(INTENT_HANDLER_MAP["search_context"], "llm_with_context")


if __name__ == "__main__":
    unittest.main()
