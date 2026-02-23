# V8 Spec — Kiểm tra flow LLM, Intent, Context & Đề xuất cải tiến

Tài liệu này tổng hợp kết quả kiểm tra các flow gọi LLM / không dùng LLM, các loại intent và cách thực thi, cách build context để trả lời user, kèm đề xuất cải tiến theo từng mục.

---

## 1. Flow gọi LLM hiện tại vs flow không dùng LLM

### 1.1. Các flow **có gọi LLM**

| Vị trí | Mục đích | Ghi chú |
|--------|----------|--------|
| **Router (3 bước)** | | |
| `SmartAIRouter.intent_only_classifier` | Phân loại intent từ câu hỏi + lọc rule liên quan | 1 gọi LLM |
| `SmartAIRouter.context_planner` | Khi `needs_data`: quyết định context_needs, chapter_range, query_target... | 1 gọi LLM |
| **Router V2** | | |
| `SmartAIRouter.ai_router_pro_v2` | Một lần LLM trả về intent + đủ tham số (context_needs, chapter_range, ...) | 1 gọi LLM, dùng khi cần fallback / đầy đủ |
| **Chat — trả lời chính** | | |
| `web_search` | Build context = kết quả web search (API, không LLM) → **LLM trả lời** | 1 LLM |
| `chat_casual` | Context = persona (+ optional semantic data) → **LLM trả lời** | 1 LLM |
| `search_context` | `ContextManager.build_context` (không LLM) → **LLM trả lời**; có thể thêm `is_answer_sufficient` (LLM) + fallback đọc full chapter → **LLM retry** | 1–3 LLM |
| `query_Sql` | `build_query_sql_context` (chỉ DB, không LLM) → **LLM trả lời** | 1 LLM |
| `numerical_calculation` | Build context → **LLM sinh code Python** → PythonExecutor → context += result → **LLM trả lời** | 2 LLM |
| `check_chapter_logic` | `run_chapter_logic_check` (build context + **LLM soát lỗi**) → context = kết quả text → **LLM trả lời** | 2 LLM (1 trong logic check, 1 trả lời) |
| `update_data` (confirm) | Context = hướng dẫn xác nhận → **LLM trả lời** ngắn (nếu có gọi trả lời) | 1 LLM (nếu có) |
| **V7 Planner** | | |
| `SmartAIRouter.get_plan_v7` | Sinh plan nhiều bước (steps + intent + args) | 1 LLM |
| `execute_plan` (từng step) | Mỗi step: build_context; step `numerical_calculation`: **LLM sinh code** → executor | N LLM (theo số step numerical) |
| Sau execute_plan | **LLM draft** câu trả lời từ cumulative context | 1 LLM |
| `run_verification_loop` | `verify_output` → có thể **LLM judge** (`_verify_grounding_llm`); nếu fail → **LLM correction** | 0–2×retries LLM |
| **Verifier** | | |
| `_verify_grounding_llm` | LLM-as-judge: response có chỉ dựa trên context không | 1 LLM / lần verify |
| **Evaluate** | | |
| `is_answer_sufficient` | Heuristic trước; không kết luận được → **LLM** sufficient true/false | 0 hoặc 1 LLM |
| `replan_after_step` (evaluate.py) | Đánh giá có nên re-plan → **LLM** sinh plan mới | 1 LLM / lần replan |
| **Background / tính năng khác** | | |
| `run_unified_chapter_analyze` | **LLM** unified extract (bible, timeline, chunks, relations, chunk_bible, chunk_timeline) | 1 LLM / chương |
| `run_chapter_logic_check` | Build context (DB) + **LLM** soát 5 dimension (timeline, bible, relation, chat_crystallize, rule) | 1 LLM / chương |
| `PersonaExtractionService` / `_call_extractor_llm` | Chuẩn hóa / trích xuất từ raw content (Excel/CSV, ...) | 1 LLM / chunk |
| Python Executor (@@) | **LLM** sinh code → thực thi | 1 LLM |
| Các view khác | `ai/content.py`, `views/data_analyze.py`, `views/review.py`, `views/bible.py`, `ai/rule_mining.py` | Nhiều điểm gọi LLM theo tính năng |

### 1.2. Các flow **không dùng LLM** để tạo câu trả lời chính

| Flow | Cách xử lý |
|------|------------|
| **ask_user_clarification** | Chỉ hiển thị `clarification_question` đã có từ router (LLM đã chạy ở bước 1). Không gọi LLM để “trả lời” user. |
| **suggest_v7** | Chỉ hiển thị template `get_v7_reminder_message()`. Không gọi LLM trả lời. |
| **Chỉ lệnh @@** | `parse_command` → status `incomplete`/`unknown` → hiển thị `get_fallback_clarification(parse_result)`. Không gọi LLM. |
| **Build context** | `ContextManager.build_context`: chỉ DB, Supabase, web_search API, `run_chapter_logic_check` (bên trong có LLM nhưng output là text đưa vào context). Không có “LLM trả lời user” trong build_context. |
| **query_Sql — phần lấy dữ liệu** | `build_query_sql_context`: chỉ truy vấn DB theo `query_target` (chapters, rules, bible_entity, chunks, timeline, relation, summary, art). Không gọi LLM. (LLM chỉ dùng sau đó để trả lời dựa trên block text.) |

---

## 2. Các loại intent và cách code thực thi theo từng intent

Định nghĩa intent nằm trong `ai/router.py` (và `INTENTS_NO_DATA`). Thực thi theo intent nằm ở `views/chat.py`, `core/executor_v7.py`, `ai_engine.py` (build_context).

### 2.1. Bảng tóm tắt

| Intent | Cần DB/context? | Luồng thực thi (code) | Có LLM trả lời? |
|--------|------------------|------------------------|------------------|
| **ask_user_clarification** | Không | Router (LLM) → hiển thị `clarification_question` trong chat, lưu history. | Không (chỉ hiển thị text từ router) |
| **web_search** | Không (chỉ API ngoài) | Build context = `do_web_search(rewritten_query)` → LLM trả lời với context = persona + kết quả search. | Có |
| **chat_casual** | Không | Context = persona (＋ optional semantic_data). LLM trả lời. | Có |
| **suggest_v7** | Không | Hiển thị `get_v7_reminder_message()`, lưu history. | Không |
| **search_context** | Có | `build_context` với context_needs/context_priority, chapter_range, target_bible_entities → chapter, bible, relation, timeline, chunk, reverse lookup. LLM trả lời. Có thể `is_answer_sufficient` (LLM) + fallback load full chapter + LLM retry. | Có (1–3 lần) |
| **query_Sql** | Có | `build_query_sql_context(router_result, project_id, arc_id)` → block text từ DB. LLM trả lời dựa trên block. | Có |
| **update_data** | Tùy | **Unified:** trigger job `unified_chapter_analyze` (LLM bên trong). **Remember rule:** context = update_summary + hướng dẫn xác nhận; có thể LLM trả lời ngắn. | Có (unified: LLM trong job; confirm: 1 LLM nếu trả lời) |
| **numerical_calculation** | Có | `build_context` → LLM sinh code Python → `PythonExecutor.execute` → context += executor result → LLM trả lời. Trong V7: step tương tự trong `execute_plan`. | Có (2 LLM) |
| **check_chapter_logic** | Có | `run_chapter_logic_check` (build context từ timeline/bible/relation/chat_crystallize/rule + **LLM** soát) → context = chuỗi kết quả (issues text). LLM trả lời dựa trên context đó. | Có (2 LLM) |

### 2.2. Chi tiết theo từng intent (file / hàm chính)

- **ask_user_clarification:** `views/chat.py`: sau khi có `router_out`, `if intent == "ask_user_clarification"` → hiển thị info + text_input, insert 2 message vào history. Không gọi `AIService.call_openrouter` cho nội dung trả lời.
- **web_search:** `views/chat.py` `elif intent in ("web_search", "chat_casual")` → web_search gọi `do_web_search`, system_content += search text → `AIService.call_openrouter(..., stream=True)`.
- **chat_casual:** Cùng branch với web_search; context chỉ persona (và optional semantic).
- **suggest_v7:** `elif intent == "suggest_v7"` → st.warning(get_v7_reminder_message()), insert history. Không LLM trả lời.
- **search_context:** `else` branch: `ContextManager.build_context(router_out, ...)` → `messages` = system (instruction + context_text) + user (prompt) → `AIService.call_openrouter(..., stream=True)`. Sau đó có thể `is_answer_sufficient` → fallback `load_chapters_by_range` + retry LLM.
- **query_Sql:** Trong `build_context`, intent `query_Sql` → `build_query_sql_context` (chỉ DB). Sau đó như search_context: context_text đưa vào system message, LLM trả lời.
- **update_data:** Unified: `_start_data_operation_background(..., unified_range=...)`; remember_rule: context là mô tả + xác nhận, có thể có đoạn trả lời ngắn bằng LLM (tùy code path).
- **numerical_calculation:** `if intent == "numerical_calculation"` → build_context → prompt sinh code → `AIService.call_openrouter` → parse code → `PythonExecutor.execute` → append result vào context_text → LLM trả lời (cùng branch với search_context).
- **check_chapter_logic:** Trong `build_context`, intent `check_chapter_logic` → `run_chapter_logic_check(...)` (gọi LLM bên trong) → context_parts.append(lines). Sau đó LLM trả lời như các intent có context.

Trong **V7** (`core/executor_v7.py`): mỗi step được map qua `step_to_router_result` → `ContextManager.build_context`; step `update_data` (unified) chỉ ghi “chờ xác nhận” vào cumulative, không build context chi tiết; step `numerical_calculation` có thể gọi LLM sinh code + executor; cuối plan gọi LLM draft + `run_verification_loop`.

---

## 3. Context dùng để trả lời câu hỏi user

Context được xây trong `ContextManager.build_context(router_result, project_id, persona, ...)` (`ai_engine.py`). Thành phần chung và theo intent:

### 3.1. Thành phần chung

- **Persona:** `persona['role']`, `persona['core_instruction']`.
- **Free chat mode:** Chỉ persona + mandatory rules + dòng “[CHẾ ĐỘ CHAT TỰ DO ...]”.
- **Arc scope (V6):** Nếu `current_arc_id`: Past Arc Summaries + Current Arc (macro context).
- **Strict mode:** Đoạn hướng dẫn “CHỈ trả lời dựa trên CONTEXT”, không bịa.
- **Rules:** `included_rules_text` (từ planner) hoặc `get_mandatory_rules(project_id)`.

### 3.2. Theo intent (trong build_context)

- **web_search:** Thêm block từ `do_web_search(rewritten_query)` (text từ API).
- **ask_user_clarification:** Thêm “[CẦN LÀM RÕ]” + clarification_question.
- **update_data:** Thêm mô tả thao tác (unified/rule) + “CẦN XÁC NHẬN”.
- **query_Sql:** `build_query_sql_context(router_result, project_id, arc_id)` → một block text (chapters / rules / bible_entity / chunks / timeline / relation / summary / art).
- **check_chapter_logic:** Gọi `run_chapter_logic_check` → block “[KẾT QUẢ SOÁT LOGIC - Chương X]” + danh sách issues (dimension, message) hoặc “Không phát hiện lỗi logic”.
- **search_context:**
  - **context_priority** quyết định thứ tự: chapter → bible/relation → timeline → chunk.
  - **chapter:** `_resolve_chapter_range` → `load_chapters_by_range` (hoặc load_full_content nếu target_files).
  - **bible/relation:** `target_bible_entities` + `HybridSearch.smart_search_hybrid_raw`, `get_entity_relations`; nếu không có entity thì search theo `rewritten_query`; có thể lọc theo chapter range.
  - **timeline:** `get_timeline_events(project_id, limit=20, chapter_range, arc_id)` → format “[TIMELINE EVENTS ...]”.
  - **chunk:** `search_chunks_vector` → `build_context_with_chunk_reverse_lookup` (nếu có); fallback parse chapter từ query + load_chapters_by_range.
  - Reverse lookup: từ `story_bible.source_chapter` → load thêm chapter liên quan entity.

Giới hạn: `max_context_tokens` (từ settings Context Size); khi vượt thì `cap_context_to_tokens`. Trong search_context có `_over_budget()` (0.92 × max) để dừng bổ sung từng need.

### 3.3. Nơi dùng context sau khi build

- Chat (V Work / V Home): system message = persona + “THÔNG TIN NGỮ CẢNH (CONTEXT):” + context_text + “HƯỚNG DẪN: …”.
- V7: cumulative_context = nối các block từng step → system_content = persona + “CONTEXT (Các bước đã thực thi)” + cumulative_context → LLM draft; verifier dùng cùng cumulative_context.

---

## 4. Đề xuất cải tiến theo từng mục

### 4.1. Flow LLM vs không LLM

- **Thống kê và tối ưu số lần gọi LLM:**  
  - Trong một lượt chat có thể có: 1 (intent) + 1 (planner) + 1 (trả lời) + 0–1 (is_answer_sufficient) + 0–1 (fallback retry) + 0–2 (verifier/correction). Nên có cơ chế (config/feature flag) giới hạn tổng số lần gọi mỗi turn (ví dụ tối đa 5) để tránh tốn chi phí và latency.
- **Tách rõ “router path” và “V7 path”:**  
  - Document rõ khi nào dùng 3 bước (intent → planner → build + LLM), khi nào dùng V2 một lần, khi nào dùng V7; tránh gọi cả intent_only + context_planner + ai_router_pro_v2 trong cùng một request.
- **ask_user_clarification / suggest_v7:**  
  - Đã không dùng LLM để trả lời là hợp lý. Có thể bổ sung vài template phụ (ví dụ gợi ý câu hỏi mẫu) mà vẫn không cần LLM.
- **Cache / reuse:**  
  - Cân nhắc cache kết quả intent + context_planner cho cùng (project_id, prompt_hash) trong time-to-live ngắn (vài chục giây) để tránh gọi LLM lặp khi user gửi lại gần giống.

### 4.2. Intent và cách thực thi

- **Chuẩn hóa tên intent và bảng ánh xạ:**  
  - Giữ một bảng duy nhất (ví dụ trong `ai/router.py` hoặc `core/command_parser.py`) map intent → handler (function hoặc enum), tránh if/elif rải rác nhiều chỗ (chat.py, executor_v7, ai_engine).
- **query_Sql vs search_context:**  
  - Đảm bảo router phân biệt rõ “xem/liệt kê dữ liệu thô” (query_Sql) và “hỏi tự nhiên về quan hệ/timeline” (search_context); có thể thêm unit test với vài câu mẫu.
- **numerical_calculation:**  
  - Hiện đã có PythonExecutor; có thể thêm bước “kiểm tra code an toàn” (sandbox / allowlist thư viện) trước khi execute để tránh risk.
- **check_chapter_logic:**  
  - Context soát đã đủ 5 dimension; có thể cho phép user chọn “chỉ soát dimension X” để giảm prompt và token.
- **update_data (unified):**  
  - Job chạy nền đã dùng 1 LLM unified extract; có thể thêm option “chỉ extract bible” hoặc “chỉ timeline” để tiết kiệm token khi không cần đủ 6 loại output.

### 4.3. Context build

- **Thứ tự và độ ưu tiên:**  
  - `context_priority` đã điều khiển thứ tự (chapter, bible, timeline, chunk). Nên log hoặc metrics “context_priority vs thời gian/token thực tế” để tinh chỉnh mặc định theo loại câu hỏi.
- **Giới hạn token rõ ràng theo từng need:**  
  - Hiện đã có `_over_budget()` và chia token cho chapter theo `len(context_priority)`. Nên ghi rõ trong spec: mỗi need (chapter, bible, timeline, chunk) có cap tối đa bao nhiêu token khi `max_context_tokens` đặt.
- **Trùng lặp nội dung:**  
  - Bible + chapter có thể trùng (cùng đoạn văn). Cân nhắc bước “dedupe” theo paragraph hash hoặc cắt bớt khi tổng token sắp vượt.
- **Lỗi từng phần:**  
  - Nếu một phần (ví dụ timeline) lỗi hoặc trống, vẫn tiếp tục thêm bible/chapter; đồng thời có thể thêm một dòng “[TIMELINE] Lỗi tải” vào context để LLM không bịa timeline.
- **query_Sql:**  
  - `build_query_sql_context` đã không dùng LLM; giữ nguyên. Có thể mở rộng `query_target` (ví dụ “art” đã có) và đảm bảo tất cả target có test.

### 4.4. V8 và sau này

- **Phiên bản API router/context:**  
  - Nếu thay đổi format router output (ví dụ thêm `context_version: 2`), `build_context` nên đọc version và xử lý tương thích ngược.
- **Observability:**  
  - Log mỗi bước: intent, context_needs, token_count trước/sau build, số lần gọi LLM trong turn, verification pass/fail. Giúp debug và tối ưu chi phí.
- **Feature flags:**  
  - Bật/tắt từng bước: chỉ intent (không planner), chỉ V2 (một LLM), tắt verification, tắt is_answer_sufficient, v.v. Để A/B test và rollback nhanh.

---

## 5. Tóm tắt file tham chiếu

| Thành phần | File chính |
|------------|------------|
| Router 3 bước, INTENTS_NO_DATA, ai_router_pro_v2 | `ai/router.py` |
| Build context theo intent | `ai_engine.py` (ContextManager.build_context) |
| Chat flow, phân nhánh intent | `views/chat.py` |
| V7 execute plan, numerical trong step | `core/executor_v7.py` |
| Query SQL context (không LLM) | `ai/query_sql.py` |
| Soát logic chương (LLM) | `core/chapter_logic_check.py` |
| Unified analyze (LLM) | `core/unified_chapter_analyze.py` |
| Verifier, grounding LLM | `ai_verifier.py` |
| is_answer_sufficient, replan | `ai/evaluate.py` |
| Command parse (@@) | `core/command_parser.py` |

---

*Tài liệu được tạo từ kiểm tra codebase (flow LLM, intent, context). Có thể bổ sung khi thêm intent hoặc đổi luồng.*
