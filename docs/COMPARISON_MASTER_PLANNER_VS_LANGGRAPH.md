# So sánh Master Planner vs LangGraph — Nhận định và khuyến nghị

> **Phiên bản:** 1.0  
> **Ngày:** 02/03/2025  
> **Mục tiêu:** So sánh hai hướng triển khai luồng AI — **Master Planner (custom)** vs **LangGraph (framework)** — để chọn phương án tối ưu cho V-Universal.

---

## Mục lục

1. [Định nghĩa hai phương án](#1-định-nghĩa-hai-phương-án)
2. [Bảng so sánh chi tiết](#2-bảng-so-sánh-chi-tiết)
3. [Phân tích theo từng tiêu chí](#3-phân-tích-theo-từng-tiêu-chí)
4. [Nhận định và khuyến nghị](#4-nhận-định-và-khuyến-nghị)
5. [Kết luận](#5-kết-luận)

---

## 1. Định nghĩa hai phương án

### 1.1 Master Planner (Custom)

**Luồng:**
```
User Query → [1 LLM: Master Planner] → JSON { tools_to_call: [...] }
         → [Backend: chạy 5 tools SONG SONG] → Merge & Dedupe
         → [1 LLM: Final Generation] → Response
```

**Đặc điểm:**
- Planner là **1 lần gọi LLM** (vd. Gemini Flash) với prompt cố định, trả về JSON chứa danh sách tools và tham số.
- Backend **parse JSON** và gọi 5 tools (GET_ENTITY_PROFILE, SEMANTIC_CHUNK_SEARCH, ...) **song song** (asyncio/ThreadPool).
- Không có vòng lặp tool-calling; luồng **tuyến tính**: Plan → Execute → Answer.
- Code thuần Python, không phụ thuộc framework orchestration.

### 1.2 LangGraph (Framework)

**Luồng:**
```
User Query → [LangGraph: Router node] → [Agent node: LLM + Tools]
         → LLM trả tool_calls → [Tool Executor] → append results → quay lại Agent
         → (lặp cho đến khi LLM không gọi tool nữa) → [Response Formatter] → END
```

**Đặc điểm:**
- **Graph-based**: Nodes (router, agent, tool_executor) + Edges (conditional routing).
- LLM **tự quyết định** gọi tool nào, khi nào, với tham số gì — qua **Function Calling** (DeepSeek/OpenAI format).
- Có **vòng lặp**: Agent ↔ Tool Executor cho đến khi LLM trả text thường (không còn tool_calls).
- Phụ thuộc LangGraph, LangChain; tích hợp LangSmith để trace.

---

## 2. Bảng so sánh chi tiết

| Tiêu chí | Master Planner | LangGraph |
|----------|----------------|-----------|
| **Số lần gọi LLM (trung bình)** | 2 cố định (Plan + Answer) | 2–5+ (Agent có thể gọi tool nhiều vòng) |
| **Điều khiển luồng** | Backend quyết định (theo JSON) | LLM quyết định (theo tool_calls) |
| **Chạy tools song song** | ✅ Dễ (Promise.all / gather) | ⚠️ Khó (LLM gọi tuần tự từng tool) |
| **Độ phức tạp code** | Thấp (Python thuần) | Trung bình (graph, state, nodes) |
| **Phụ thuộc bên ngoài** | Không (chỉ LLM API) | LangGraph, LangChain |
| **Durable execution** | ❌ Không có sẵn | ✅ Checkpointing, resume |
| **Human-in-the-loop** | ❌ Tự build | ✅ interrupt_before |
| **Streaming** | Tự build | ✅ stream() sẵn |
| **Observability** | Tự build / log | ✅ LangSmith tích hợp |
| **Chi phí token** | Dự đoán được (2 calls) | Khó đoán (nhiều vòng tool) |
| **Latency** | Thấp (2 calls + parallel tools) | Cao hơn (nhiều vòng, tuần tự tool) |
| **Mở rộng tools** | Thêm vào JSON schema + hàm Python | Thêm tool definition + bind |
| **Re-planning** | Planner có thể sinh lại plan | Agent tự "suy nghĩ" gọi thêm tool |
| **Phù hợp V-Universal** | Cao (luồng cố định, 5 tools) | Trung bình (overkill cho use case đơn giản) |

---

## 3. Phân tích theo từng tiêu chí

### 3.1 Chi phí và Latency

**Master Planner:**
- **2 LLM calls** cố định: Planner (nhẹ, ~500 tokens) + Final (nặng, context + answer).
- **5 tools chạy song song** → tổng thời gian ≈ max(tool_1, ..., tool_5), không phải tổng.
- Chi phí token **dự đoán được**, dễ budget.

**LangGraph:**
- Agent có thể gọi tool **nhiều vòng** (vd. search_bible → thấy thiếu → search_chunks → ...).
- Mỗi vòng = 1 LLM call. Trung bình 2–4 vòng cho câu hỏi phức tạp.
- Tools chạy **tuần tự** trong mỗi vòng → latency cao hơn.
- Chi phí token **khó kiểm soát**; có thể vượt budget khi Agent "tham" gọi nhiều tool.

**Kết luận:** Master Planner **rẻ hơn và nhanh hơn** cho use case V-Universal (5 tools cố định, không cần vòng lặp phức tạp).

---

### 3.2 Điều khiển và Dự đoán được

**Master Planner:**
- Luồng **hoàn toàn xác định**: Plan → Execute → Answer.
- Dễ debug: in JSON plan, xem tools nào được gọi, kết quả từng tool.
- Dễ A/B test: đổi prompt Planner, so sánh chất lượng plan.

**LangGraph:**
- LLM **tự quyết định** → hành vi khó dự đoán.
- Có thể gọi tool thừa, thiếu, hoặc sai tham số.
- Cần guardrail (max tool calls, validation) để tránh loop vô hạn.

**Kết luận:** Master Planner **dễ kiểm soát** hơn cho sản phẩm cần ổn định.

---

### 3.3 Chạy tools song song

**Master Planner:**
- Planner xuất **toàn bộ** tools cần gọi trong 1 JSON.
- Backend gọi `asyncio.gather` hoặc `ThreadPoolExecutor` → **song song**.
- Ví dụ: GET_ENTITY_PROFILE (200ms) + SEMANTIC_CHUNK_SEARCH (300ms) + GET_STORY_MAP (100ms) → tổng ≈ 300ms, không phải 600ms.

**LangGraph:**
- LLM gọi **từng tool một** (tool_calls trong 1 response có thể nhiều, nhưng thường 1–2).
- Sau mỗi tool result, LLM "suy nghĩ" rồi gọi tool tiếp → **tuần tự**.
- Để song song cần thiết kế đặc biệt (vd. custom node chạy nhiều tools cùng lúc), phức tạp hơn.

**Kết luận:** Master Planner **tối ưu latency** nhờ parallel tools; LangGraph mặc định tuần tự.

---

### 3.4 Độ phức tạp và Phụ thuộc

**Master Planner:**
- Code: 1 Planner prompt, 5 hàm Python, 1 Merge & Dedupe, 1 Final prompt.
- Không cần thư viện mới (chỉ OpenAI/OpenRouter client).
- Dễ đọc, dễ sửa cho dev quen Python.

**LangGraph:**
- Cần: `langgraph`, `langchain`, `langchain-openai` (hoặc tương đương).
- Khái niệm: StateGraph, nodes, edges, conditional_edges, MessagesState.
- Learning curve cho team chưa dùng LangChain.
- Phiên bản LangGraph/LangChain thay đổi nhanh → có thể breaking change.

**Kết luận:** Master Planner **đơn giản hơn**, ít rủi ro phụ thuộc.

---

### 3.5 Durable Execution và Human-in-the-loop

**Master Planner:**
- Không có sẵn. Muốn resume khi lỗi → tự build (lưu state, checkpoint).
- Muốn human xác nhận trước khi chạy unified → tự build (pause, wait input).

**LangGraph:**
- **Checkpointing** sẵn: lưu state sau mỗi node, resume khi crash.
- **interrupt_before**: dừng trước node X, chờ human xác nhận, rồi tiếp tục.
- Hữu ích cho workflow dài (vd. data pipeline nhiều bước) hoặc cần compliance.

**Kết luận:** LangGraph **vượt trội** nếu cần durable execution hoặc human-in-the-loop. V-Universal hiện tại **chưa cần** (chat 1 turn, không workflow dài ngày).

---

### 3.6 Mở rộng và Linh hoạt

**Master Planner:**
- Thêm tool: (1) Thêm vào prompt Planner, (2) Thêm hàm Python, (3) Thêm vào danh sách gọi song song.
- Đổi logic: sửa prompt Planner (vd. "khi hỏi X thì gọi tool A, B").
- Giới hạn: Planner phải "đoán" đủ tools trong 1 lần; nếu thiếu thì không có cơ chế "gọi thêm" (trừ khi thêm vòng 2).

**LangGraph:**
- Thêm tool: thêm definition vào bind, implement hàm. Agent tự học khi nào gọi.
- Đổi logic: thêm node, sửa edge, conditional routing.
- Linh hoạt: Agent có thể gọi thêm tool nếu kết quả chưa đủ (re-planning ngầm).

**Kết luận:** LangGraph **linh hoạt hơn** cho use case phức tạp, nhiều nhánh. V-Universal với **5 tools cố định** thì Master Planner đủ.

---

### 3.7 Phù hợp với Bản thiết kế V10

**Bản thiết kế** mô tả rõ:
- Bước 1: Planner xuất JSON tools.
- Bước 2: Chạy **song song** 5 tools.
- Bước 3: Merge & Dedupe.
- Bước 4: Final Generation.

→ Luồng này **khớp 1:1** với Master Planner. LangGraph mặc định **không** chạy 5 tools song song trong 1 bước; cần custom node để làm điều đó, khi đó lại gần với Master Planner.

**Kết luận:** Master Planner **bám sát** Bản thiết kế hơn.

---

## 4. Nhận định và khuyến nghị

### 4.1 Khi nào Master Planner tốt hơn

- Use case **đơn giản, cố định**: 5 tools, luồng Plan → Execute → Answer.
- Ưu tiên **chi phí thấp, latency thấp**.
- Team **nhỏ**, muốn code dễ đọc, ít phụ thuộc.
- **Không cần** durable execution, human-in-the-loop phức tạp.
- Bám sát **Bản thiết kế V10** (Ngũ Hổ Tướng song song).

→ **Master Planner phù hợp hơn** cho V-Universal ở giai đoạn hiện tại.

### 4.2 Khi nào LangGraph tốt hơn

- Use case **phức tạp, nhiều nhánh**: nhiều loại agent, routing động, workflow dài.
- Cần **Agent tự quyết định** gọi tool (không muốn Planner cố định).
- Cần **durable execution** (resume khi crash, chạy job dài).
- Cần **human-in-the-loop** (xác nhận từng bước, compliance).
- Có **LangSmith** để trace, debug, đánh giá.
- Team đã quen LangChain/LangGraph.

→ LangGraph phù hợp khi V-Universal **mở rộng** sang workflow phức tạp (vd. pipeline nhiều bước, multi-agent, approval flow).

### 4.3 Khuyến nghị cho V-Universal

**Giai đoạn 1 (hiện tại — V10):** Dùng **Master Planner**.

**Lý do:**
1. Bám sát Bản thiết kế, dễ triển khai.
2. Chi phí và latency tối ưu (2 LLM calls, tools song song).
3. Code đơn giản, ít phụ thuộc.
4. Dễ debug, A/B test Planner prompt.
5. V-Universal chưa cần durable execution hay human-in-the-loop phức tạp.

**Giai đoạn 2 (tương lai):** Cân nhắc **LangGraph** khi:
- Cần workflow dài (nhiều bước, nhiều ngày).
- Cần human xác nhận trước thao tác nhạy cảm.
- Cần multi-agent (vd. Reviewer agent, Writer agent).
- Muốn Agent tự "suy nghĩ" gọi thêm tool khi thiếu thông tin.

**Lộ trình đề xuất:**
1. Triển khai **Master Planner** (V10) — ổn định, đủ dùng.
2. Nếu sau này cần LangGraph → **tích hợp** Master Planner làm 1 node trong graph (Planner node → Tools node → Answer node). Không phải viết lại từ đầu.

---

## 5. Kết luận

| Phương án | Điểm mạnh | Điểm yếu | Phù hợp V-Universal |
|-----------|-----------|----------|---------------------|
| **Master Planner** | Đơn giản, rẻ, nhanh, song song tools, dễ kiểm soát | Không durable, không human-in-the-loop sẵn | ✅ **Cao** (giai đoạn hiện tại) |
| **LangGraph** | Durable, human-in-the-loop, linh hoạt, observability | Phức tạp hơn, tools tuần tự, chi phí khó đoán | ⚠️ **Trung bình** (giai đoạn mở rộng) |

**Kết luận:** Với bối cảnh V-Universal hiện tại (5 tools cố định, luồng Plan → Execute → Answer, ưu tiên cost/latency), **Master Planner là lựa chọn tối ưu hơn**. LangGraph nên xem xét khi hệ thống mở rộng sang workflow phức tạp hoặc cần durable execution, human-in-the-loop.

---

## Phụ lục: Kết hợp hai phương án

Có thể **kết hợp** để tận dụng ưu điểm cả hai:

- **Master Planner** làm node "Plan" trong LangGraph.
- Planner xuất JSON → node "Tools" chạy song song 5 tools → node "Merge" → node "Answer".
- Khi cần human-in-the-loop: thêm `interrupt_before` trước node "Tools" (vd. trước khi chạy unified).
- Khi cần durable: bật checkpointing cho graph.

→ Vừa giữ được **song song tools**, vừa có **LangGraph benefits** (durable, interrupt). Chi phí triển khai cao hơn Master Planner thuần.
