# V-Universe AI Hub Pro (V8) — Log tính năng & Định giá

Tài liệu mô tả chi tiết tính năng, công nghệ và gợi ý định giá cho dự án V-Universe (V8): outsource từ đầu, bán đứt, gói tháng và custom cho khách.

---

## 1. Tổng quan dự án

- **Tên:** V-Universe AI Hub Pro (phiên bản V7/V8).
- **Mục đích:** Nền tảng AI hỗ trợ sáng tác & quản lý tri thức truyện (story bible, timeline, quan hệ nhân vật, chat có ngữ cảnh, trích xuất dữ liệu, kiểm tra logic chương, tính toán số liệu, v.v.).
- **Đối tượng:** Tác giả, biên tập, team sáng tác nội dung (web novel, truyện dài kỳ).
- **Kiến trúc:** Web app (Streamlit), backend Python, Supabase (DB + Auth), OpenRouter (LLM đa model).

---

## 2. Công nghệ (Tech Stack)

| Thành phần | Công nghệ |
|------------|-----------|
| **Frontend / App** | Streamlit (Python) |
| **Backend / Logic** | Python 3.x (ai, core, views, utils) |
| **Database** | Supabase (PostgreSQL) |
| **Auth / User** | Supabase Auth, session (extra-streamlit-components) |
| **LLM** | OpenRouter API (Claude, Gemini, DeepSeek, Llama, Mistral, …) |
| **Embedding** | OpenRouter / Qwen embedding (hybrid search) |
| **File / Doc** | python-docx, pypdf, openpyxl, pandas, numpy |
| **Chạy code user** | Python executor (sandbox) cho numerical_calculation |
| **Deploy** | Streamlit Cloud / self-host (Docker, server) |

---

## 3. Tính năng chi tiết (theo module)

### 3.1. Workspace (Khu vực làm việc)

| Tính năng | Mô tả ngắn |
|-----------|------------|
| **Dashboard** | Tổng quan dự án: số file, số chương, Bible, relations, timeline; nút nhanh; cache metrics; xóa crystallize khi xóa chat. |
| **Workstation** | Soạn/thêm chương; import file (docx, pdf, txt); gợi ý metadata (LLM); trích xuất Bible từ chương (extractor persona); gửi chương sang Review. |
| **Data Analyze** | Phân tích dữ liệu dự án: extract entity từ nội dung (theo persona), gán chương; exclude existing; pipeline theo batch. |
| **Background Jobs** | Quản lý job nền: unified chapter analyze, chapter logic check, data operation; trạng thái, log, hủy. |
| **Review** | Review chương bằng AI (persona review_prompt); chấm điểm, điểm mạnh/yếu, lời khuyên. |
| **Python Executor** | Chạy code Python (pandas/numpy) an toàn trong sandbox; dùng cho numerical_calculation từ chat; UI test executor. |

### 3.2. Knowledge (Tri thức / Dữ liệu)

| Tính năng | Mô tả ngắn |
|-----------|------------|
| **Bible** | Story Bible: CRUD nhân vật, địa danh, sự kiện, mối quan hệ; tag, trích dẫn; [CHAT] crystallize từ chat; import/export. |
| **Relations** | Quan hệ giữa các entity (Bible); đồ thị/quan hệ; đồng bộ với Bible. |
| **Chunking** | Chia văn bản thành chunk; embedding; hybrid search (full-text + vector); reverse lookup phục vụ search_context. |
| **Rules** | Luật nội bộ (rule mining từ chat); gợi ý merge; áp dụng khi build context (strict mode). |
| **Memory (Chat Management)** | Quản lý lịch sử chat, crystallize (sau N tin nhắn ghi vào Bible [CHAT]); xóa theo topic. |
| **Arc** | Cấu trúc Arc (parent/child); metadata hiển thị; liên kết với chương. |
| **Timeline** | Sự kiện theo thời gian; tích hợp context cho LLM; limit 30 (V8). |
| **Chỉ lệnh (Commands)** | Chỉ lệnh @@ (ví dụ @@bible, @@timeline); fallback không LLM; gợi ý làm rõ khi câu quá ngắn. |
| **Data Health** | Kiểm tra sức khỏe dữ liệu: Bible, relations, timeline, chunk; báo thiếu/ lỗi. |
| **Semantic Intent** | Mẫu câu hỏi → intent (chat_casual, search_context…); thêm từ chat; ưu tiên khi match. |

### 3.3. Chat (V Work / V Home)

| Tính năng | Mô tả ngắn |
|-----------|------------|
| **V Work** | Chat theo dự án: persona (Writer/Editor…), context từ Bible, relations, timeline, chunk, chapter; có ghi nhớ/cập nhật dữ liệu (update_data). |
| **V Home** | Chat tổng (không dính dự án cụ thể); lịch sử theo topic; không search context dự án. |
| **Router & Planner** | Intent classification (1 LLM); context_planner hoặc get_plan_v7_light (tối đa 3 bước); execute_plan + draft. |
| **Search context (V8)** | Full context: chapter, bible, relation, timeline, chunk; bổ sung toàn văn chương khi token < 35% budget; is_answer_sufficient → fallback đọc full chapter rồi mới hiển thị (không “hiện câu ngắn rồi đổi”). |
| **Intent xử lý** | search_context, query_Sql, numerical_calculation, check_chapter_logic, update_data, web_search, chat_casual, ask_user_clarification, suggest_v7. |
| **Verification** | LLM-as-judge kiểm tra response bám context; correction loop (tối đa retry). |
| **Giới hạn LLM/turn** | max_llm_calls_per_turn (Settings); verification/sufficient không tính. |
| **Cost/Budget** | Tính token, cost theo model; trừ budget user (Cost tab). |

### 3.4. Admin (Quản trị)

| Tính năng | Mô tả ngắn |
|-----------|------------|
| **Collaboration** | Quản lý thành viên, vai trò, chia sẻ dự án (nếu có). |
| **Cost** | Xem chi phí LLM, budget user, lịch sử tiêu thụ. |
| **Settings** | Model mặc định, context size, temperature; V8 & Observability: V8 full context search, max_llm_calls_per_turn; Observability (chat_turn_logs). |

### 3.5. Hạ tầng / Core

| Thành phần | Mô tả ngắn |
|------------|------------|
| **AI Router** | SmartAIRouter: intent_only_classifier, context_planner, get_plan_v7_light, ai_router_pro_v2; INTENT_HANDLER_MAP. |
| **Context** | ContextManager.build_context (chapter, bible, relation, timeline, chunk, reverse lookup); cap token; V8 full context + bổ sung chương. |
| **Executor V7** | execute_plan (multi-step); numerical với llm_budget_ref; build_context từng bước. |
| **Chapter logic check** | Kiểm tra mâu thuẫn logic chương (timeline, bible, relation, chat, rule); dimension; background job. |
| **Unified chapter analyze** | Phân tích chương hàng loạt (extract, metadata); job nền. |
| **Observability** | log_chat_turn (intent, context_needs, context_tokens, llm_calls_count) → chat_turn_logs. |
| **Rule mining** | Trích xuất luật từ chat; gợi ý merge. |
| **Auth / Session** | Supabase auth; SessionManager; cache sidebar/cost. |

---

## 4. Định giá (gợi ý)

Đơn vị: **VND** (có thể quy đổi USD theo tỷ giá). Giá dưới đây mang tính tham khảo cho thị trường outsource Việt Nam và bán sản phẩm/SaaS.

### 4.1. Outsource làm từ đầu (Custom development từ zero)

- **Mô tả:** Khách thuê team build một hệ thống tương đương V8 (theo spec tính năng trên).
- **Phạm vi:** Toàn bộ tính năng tương đương (Workspace + Knowledge + Chat + Admin + Core).
- **Ước lượng công:** ~ 6–12 tháng (2–4 dev fullstack/backend + 1 part-time frontend/UI), tùy quy mô team và mức độ chi tiết spec.
- **Gợi ý định giá:**
  - **Tổng dự án:** 800.000.000 – 2.000.000.000 VND (~ 32.000 – 80.000 USD).
  - **Theo tháng (theo team):** 120.000.000 – 220.000.000 VND/tháng (~ 4.800 – 8.800 USD/tháng), 6–10 tháng.

### 4.2. Bán đứt (Mua source code / bản quyền sản phẩm)

- **Mô tả:** Khách mua bản quyền source code và tài sản sản phẩm (V8 như hiện tại), tự host và tùy biến.
- **Bao gồm:** Source code, tài liệu kỹ thuật (spec, changelog), hướng dẫn deploy; có thể kèm bàn giao ngắn (training 1–2 ngày).
- **Không bao gồm:** Bảo trì dài hạn, feature mới, support sau bàn giao (có thể bán riêng gói bảo trì).
- **Gợi ý định giá:**
  - **Mức 1 (single team/startup):** 350.000.000 – 600.000.000 VND (~ 14.000 – 24.000 USD).
  - **Mức 2 (enterprise, kèm tùy biến tên thương hiệu / tích hợp sẵn):** 600.000.000 – 1.200.000.000 VND (~ 24.000 – 48.000 USD).

### 4.3. Gói tháng (SaaS / Thuê theo tháng)

- **Mô tả:** Khách dùng sản phẩm đã deploy (multi-tenant hoặc instance riêng), trả phí định kỳ; bao gồm hosting, bảo trì cơ bản, cập nhật lỗi.
- **Gợi ý định giá (theo tháng):**
  - **Starter (cá nhân / 1 dự án, giới hạn token/user):** 500.000 – 1.000.000 VND/tháng (~ 20 – 40 USD/tháng).
  - **Team (3–10 user, vài dự án):** 2.000.000 – 5.000.000 VND/tháng (~ 80 – 200 USD/tháng).
  - **Business (unlimited project, API/embed, SLA):** 8.000.000 – 20.000.000 VND/tháng (~ 320 – 800 USD/tháng).
- **Lưu ý:** Chi phí LLM (OpenRouter) thường tính thêm theo usage hoặc gói token (để không lỗ).

### 4.4. Custom cho khách (Chỉ làm phần tùy biến / mở rộng)

- **Mô tả:** Khách đã có bản V8 (mua đứt hoặc đang dùng); yêu cầu thêm tính năng, tích hợp, đổi flow, báo cáo riêng, v.v.
- **Định giá:** Theo ngày công hoặc theo gói feature.
  - **Theo ngày công:** 2.500.000 – 4.500.000 VND/ngày (~ 100 – 180 USD/ngày) tùy seniority.
  - **Ví dụ gói:**
    - Tích hợp SSO / LDAP: 15–30 ngày → 45.000.000 – 120.000.000 VND.
    - Thêm 1 module báo cáo (dashboard, export): 10–20 ngày → 30.000.000 – 80.000.000 VND.
    - Tối ưu prompt / thêm 1 intent mới: 5–10 ngày → 15.000.000 – 45.000.000 VND.
    - Custom persona + luật rule theo nghiệp vụ: 3–7 ngày → 10.000.000 – 30.000.000 VND.

---

## 5. Tóm tắt bảng giá (tham khảo)

| Hình thức | Khoảng giá (VND) | Ghi chú |
|-----------|------------------|--------|
| **Outsource từ đầu** | 800M – 2B | 6–12 tháng, full tính năng tương đương V8 |
| **Bán đứt** | 350M – 1,2B | Source + bàn giao, tùy đối tượng khách |
| **Gói tháng (SaaS)** | 500k – 20M/tháng | Theo tier Starter / Team / Business |
| **Custom (theo ngày)** | 2,5M – 4,5M/ngày | Feature/tích hợp phát sinh |

---

*Tài liệu: **V8-log-price** — Log tính năng & định giá V-Universe AI Hub Pro. Cập nhật theo codebase và spec V8.1/V8.3.*
