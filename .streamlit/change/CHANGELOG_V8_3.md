# Changelog V8.3 — Full search context, Observability, Feature flags, Intent map

**Áp dụng:** Chạy migration `.streamlit/data/schema_v8_3_migration.sql` (tạo bảng `chat_turn_logs`).  
**Spec tham chiếu:** `docs/v8-spec.md`

---

## 1. Cải tiến Search Context (intent search_context)

- **Luôn gather đủ nguồn:** Khi intent là `search_context`, context không còn phụ thuộc vào một loại (chỉ chapter hoặc chỉ bible). Hệ thống **luôn thu thập đủ**: chapter, bible, relation, timeline, chunk (theo thứ tự `FULL_CONTEXT_NEEDS_V8`).
- **Không phân loại một loại:** Bỏ logic “chỉ áp dụng đúng một loại search” — user không cần chỉ định rõ “chỉ tìm bible” hay “chỉ timeline”; tất cả được đưa vào context để trả lời đủ ý.
- **Tăng cường chunk & timeline:**
  - Chunk: `top_k=20` (trước 10), `token_limit=6000` cho reverse lookup (trước 5000).
  - Timeline: `limit=30` (trước 20).
- **Bổ sung toàn văn chương khi context thiếu:** Sau khi đã gather chapter, bible, relation, timeline, chunk, nếu tổng token vẫn dưới **35%** `max_context_tokens` thì tự động load thêm **toàn văn chương** (theo `chapter_range`) và append vào context với nhãn `[V8 bổ sung toàn văn khi context còn thiếu]`.
- **Feature flag:** Trong **Settings → V8 & Observability** có toggle **V8 Full context search**. Bật (mặc định): dùng full gather; Tắt: dùng lại `context_needs` / `context_priority` từ Router (hành vi cũ).

**File thay đổi:** `ai_engine.py`, `ai/context_schema.py` (thêm `FULL_CONTEXT_NEEDS_V8`).

---

## 2. Database migration 8.3

- **Bảng `chat_turn_logs`:**
  - `id` (UUID PK), `story_id` (FK stories, nullable), `user_id` (TEXT, nullable).
  - `intent`, `context_needs` (JSONB), `context_tokens` (INT), `llm_calls_count` (INT), `verification_used` (BOOLEAN).
  - `created_at` (TIMESTAMPTZ).
  - Index: story_id, user_id, created_at DESC.
- Dùng cho **observability**: mỗi turn chat (khi build context xong và gọi LLM trả lời) ghi một dòng log.

**File:** `.streamlit/data/schema_v8_3_migration.sql`

---

## 3. Observability

- **Module:** `core/observability.py` — hàm `log_chat_turn(story_id, user_id, intent, context_needs, context_tokens, llm_calls_count, verification_used)`.
- **Gọi từ:** `views/chat.py` sau khi build context và trước khi gọi `AIService.call_openrouter` (nhánh trả lời chính với context). Log: intent, context_needs, context_tokens, llm_calls_count=1.
- Nếu bảng `chat_turn_logs` chưa tồn tại hoặc lỗi, log bị bỏ qua (try/except, in lỗi ra console).

---

## 4. Feature flags & Settings UI

- **Tab mới:** **Settings → V8 & Observability** (tab thứ 6).
- Nội dung:
  - Mô tả V8.3: search context gather đủ bible/chunk/relation/timeline/chapter và bổ sung toàn văn chương khi thiếu.
  - Toggle **V8 Full context search**: bật = dùng full gather; tắt = dùng context_needs từ Router. Lưu vào `settings` key `v8_full_context_search` (value `"1"` / `"0"`).
  - Ghi chú Observability: mỗi turn ghi vào `chat_turn_logs`; cần chạy migration V8.3.
- **Đọc flag trong code:** `ai_engine.py` — khi intent `search_context`, đọc `settings.v8_full_context_search`; nếu `"0"` thì không override `context_needs` / `context_priority` (dùng từ router).

**File:** `views/settings.py`, `ai_engine.py`.

---

## 5. Intent handler map (V8 spec 4.2)

- **Bảng ánh xạ:** Trong `ai/router.py` thêm `INTENT_HANDLER_MAP`: intent → handler type.
  - `clarification`: ask_user_clarification.
  - `template`: suggest_v7.
  - `llm_casual`: web_search, chat_casual.
  - `llm_with_context`: search_context, query_Sql, numerical_calculation, check_chapter_logic.
  - `data_operation`: update_data.
- Dùng để tham chiếu thống nhất; `views/chat.py` và `core/executor_v7.py` vẫn dùng `intent` trực tiếp (chưa refactor sang handler name).

**File:** `ai/router.py`.

---

## 6. Tóm tắt file thay đổi / thêm mới

| File | Thay đổi |
|------|----------|
| `ai/context_schema.py` | Thêm `FULL_CONTEXT_NEEDS_V8`. |
| `ai_engine.py` | search_context: override context_needs/priority sang full list (khi flag bật); tăng chunk top_k/token_limit, timeline limit; thêm block bổ sung toàn văn chương khi token < 35% max; đọc `v8_full_context_search` từ settings. |
| `ai/router.py` | Thêm `INTENT_HANDLER_MAP`. |
| `views/chat.py` | Import `log_chat_turn`; gọi `log_chat_turn(...)` trước khi gọi LLM trả lời (nhánh có context). |
| `views/settings.py` | Thêm tab **V8 & Observability**, toggle và nút lưu `v8_full_context_search`. |
| `core/observability.py` | **Mới** — `log_chat_turn()`. |
| `.streamlit/data/schema_v8_3_migration.sql` | **Mới** — bảng `chat_turn_logs`. |
| `.streamlit/change/CHANGELOG_V8_3.md` | **Mới** — log thay đổi V8.3. |

---

## 7. Giới hạn LLM/turn + Planner 3 bước + Tối ưu verification (bổ sung)

- **Số lần gọi LLM tối đa mỗi turn:** User cấu hình trong **Settings → V8 & Observability** (number input), mặc định **5**. Chỉ tính các gọi "chính": intent_only, context_planner, get_plan_v7_light, draft, numerical code gen. **Verification và check (verify_output, is_answer_sufficient) không tính** vào giới hạn.
- **Khi đạt giới hạn:** Không gọi thêm LLM; hiển thị thông báo "(Đã đạt giới hạn X lần gọi LLM...)" và gợi ý tăng trong Settings.
- **Planner theo mô hình 3 bước (như Router), không lag:**
  - **Bước 1:** Luôn gọi `intent_only_classifier` (1 LLM) chung cho cả Router và Planner.
  - **Bước 2:** Nếu bật V7 Planner và (intent = suggest_v7 hoặc câu hỏi multi-intent): gọi `get_plan_v7_light` (prompt ngắn, tối đa 3 bước, 1 LLM). Nếu không: dùng `context_planner` khi needs_data (1 LLM) hoặc router_out từ step1.
  - **Bước 3:** Execute (build context không LLM) + 1 LLM draft/trả lời. Numerical trong execute_plan chỉ gọi LLM khi còn budget (`llm_budget_ref`).
- **execute_plan:** Thêm tham số `llm_budget_ref: Optional[List[int]]` = [current_count, max_count]. Khi gọi LLM cho numerical_calculation kiểm tra và tăng current; khi hết budget bỏ qua numerical LLM và ghi "(Bỏ qua: đã đạt giới hạn...)".
- **Tối ưu verification (nhanh, tiết kiệm):** `_verify_grounding_llm`: `max_context_chars=6000`, `resp_slice` cắt 2500, `max_tokens=150` (trước 10000, 4000, 200).

**File thay đổi:** `config.py` (get_max_llm_calls_per_turn), `views/settings.py` (input + lưu max_llm_calls_per_turn), `views/chat.py` (counter, 3-step thống nhất, check trước mỗi LLM), `ai/router.py` (get_plan_v7_light), `core/executor_v7.py` (llm_budget_ref), `ai_verifier.py` (giảm token verification).

---

*Các cải tiến khác trong v8-spec (cache intent/planner, dedupe context, numerical code safety, v.v.) có thể thực hiện trong bản tiếp theo.*
