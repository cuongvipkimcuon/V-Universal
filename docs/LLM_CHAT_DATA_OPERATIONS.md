# Hướng dẫn LLM: Xử lý dữ liệu qua Chat (Unified)

Tài liệu này trích các phần **prompt/instruction** liên quan đến **update_data** và **unified** để bạn tinh chỉnh khi dùng chat để xử lý dữ liệu (phân tích chương). Code đã chuyển toàn bộ thao tác theo chương sang **Unified** (1 lần LLM → Bible + Timeline + Chunks + Relations); không còn từng bước bible / relation / timeline / chunking riêng lẻ.

---

## 1. Router (ai/router.py) — Bảng intent update_data

Đoạn trong **router_prompt** (bảng quy tắc chọn intent):

| INTENT | Mô tả |
| :--- | :--- |
| **update_data** | (1) **Ghi nhớ quy tắc**: "Hãy nhớ rằng...", "Cập nhật quy tắc..." → data_operation_type: "remember_rule", data_operation_target: "rule", update_summary: mô tả. (2) **Thao tác theo chương (Unified)**: phân tích/chạy **unified** cho một hoặc nhiều chương (một lần LLM → Bible + Timeline + Chunks + Relations) → data_operation_type: "extract", data_operation_target: "unified", chapter_range [start, end] hoặc [ch, ch]. |

**Từ khóa gợi ý:** "Hãy nhớ rằng...", "Unified chương 1", "Phân tích dữ liệu chương 1 đến 10", "Chạy unified chương 5".

---

## 2. Router — Output schema (data_operation)

- `data_operation_type`: "" hoặc "remember_rule" | "extract" | "update" | "delete" (khi intent = update_data).
- `data_operation_target`: "" hoặc **"rule"** | **"unified"** (không còn bible | relation | timeline | chunking).
- Khi thao tác theo chương: luôn dùng **data_operation_target: "unified"** và **chapter_range: [start, end]** (một chương thì [ch, ch]).

---

## 3. Router — Quy tắc đặc biệt

- **Quy tắc "Chương / đọc nội dung":** User **ra lệnh thao tác dữ liệu** (extract/update/delete) theo chương → `update_data` với data_operation_target: "unified", chapter_range.
- **Quy tắc "update_data":** Chỉ khi user **ra lệnh thay đổi/ghi dữ liệu**. Chỉ xem/tóm tắt/hỏi → KHÔNG update_data.

---

## 4. Router — Logic chapter_range

- "Chương 1", "Chap 5" → chapter_range: [1, 1] hoặc [5, 5], chapter_range_mode: "range".
- "Chương 1 đến 5" → chapter_range: [1, 5], chapter_range_mode: "range".
- "3 chương đầu" → chapter_range_mode: "first", chapter_range_count: 3.
- "Chương mới nhất" → chapter_range_mode: "latest", chapter_range_count: 1.

---

## 5. Router — Ví dụ few-shot (Unified)

**Input:** "Chạy unified chương 1 đến 10"  
**Output:** intent: "update_data", data_operation_type: "extract", data_operation_target: "unified", chapter_range: [1, 10], chapter_range_mode: "range", (các key khác đủ theo schema).

**Input:** "Phân tích dữ liệu chương 5"  
**Output:** intent: "update_data", data_operation_type: "extract", data_operation_target: "unified", chapter_range: [5, 5], chapter_range_mode: "range".

---

## 6. V7 Planner (ai/router.py — get_plan_from_llm)

- **plan** mỗi bước có **args** chứa: data_operation_type, data_operation_target, chapter_range.
- Khi user yêu cầu unified theo chương, plan nên có **một bước** intent: "update_data", args.data_operation_target: "unified", args.chapter_range: [start, end].
- Code đã chuẩn hóa: intent cũ "extract_bible" / "extract_relation" / ... → chuyển thành intent "update_data" với data_operation_target: "unified".

---

## 7. Gợi ý tinh chỉnh

- **Từ khóa nhận diện:** Thêm/cập nhật cụm từ user hay dùng (vd: "cập nhật dữ liệu", "chạy phân tích", "unified từ chương X đến Y") vào bảng intent và ví dụ.
- **Chapter_range mở rộng:** Nếu muốn hỗ trợ "5 chương đầu", "chương mới nhất" trong unified, đảm bảo logic trích chapter_range (first/latest/count) được chat/backend giải quyết thành [start, end] trước khi tạo job.
- **Planner:** Nếu V7 trả về nhiều bước update_data (vd nhiều khoảng chương), hiện tại chỉ bước đầu được dùng để tạo job unified_chapter_range; có thể mở rộng sau (nhiều job hoặc gộp nhiều range).

---

File code tham chiếu:

- **Router prompt:** `ai/router.py` — biến `router_prompt` (và bảng intent, schema, few-shot).
- **Planner:** `ai/router.py` — `get_plan_from_llm`, chuẩn hóa plan (extract_bible → update_data + unified).
- **Chat xử lý:** `views/chat.py` — nhánh intent == "update_data", và V7 block "chỉ update_data" (unified_range_v7).
- **Executor V7:** `core/executor_v7.py` — bước update_data với data_operation_target == "unified" (thu thập data_operation_steps).
- **Chỉ lệnh @@:** `core/command_parser.py` — BUILTIN_TRIGGERS, COMMAND_TO_ROUTER ("unified", "data_analyze" → update_data + unified).
