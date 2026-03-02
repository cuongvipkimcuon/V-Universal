# Kế hoạch nâng cấp luồng AI lên LangGraph và DeepSeek AI Native Tools

> **Phiên bản:** 1.0  
> **Ngày:** 02/03/2025  
> **Mục tiêu:** Nâng cấp toàn bộ luồng AI hiện tại sang LangGraph (orchestration) và sử dụng DeepSeek AI Native Tools (Function Calling) cho các thao tác cần thiết.

---

## Mục lục

1. [Tổng quan kiến trúc AI hiện tại](#1-tổng-quan-kiến-trúc-ai-hiện-tại)
2. [Phân tích điểm yếu và cơ hội](#2-phân-tích-điểm-yếu-và-cơ-hội)
3. [LangGraph và DeepSeek AI Native Tools](#3-langgraph-và-deepseek-ai-native-tools)
4. [Kiến trúc mục tiêu](#4-kiến-trúc-mục-tiêu)
5. [Kế hoạch triển khai chi tiết](#5-kế-hoạch-triển-khai-chi-tiết)
6. [Định nghĩa Tools cho DeepSeek](#6-định-nghĩa-tools-cho-deepseek)
7. [Lộ trình và mốc thời gian](#7-lộ-trình-và-mốc-thời-gian)

---

## 1. Tổng quan kiến trúc AI hiện tại

### 1.1 Luồng tổng thể (V6 / V7)

```
User Input → [Intent Classifier] → [Context Planner] → [Intent Handler] → [ContextManager.build_context] → [LLM Response]
                    ↓                        ↓
              (1 LLM call)             (1 LLM call)
```

**V7 Planner mode (multi-step):**
```
User Input → [Intent Classifier] → [V7 Planner] → [Executor V7] → [Step 1..N] → [LLM Response]
                    ↓                    ↓              ↓
              (1 LLM call)         (1 LLM call)   (N LLM calls)
```

### 1.2 Các thành phần chính

| Thành phần | File | Chức năng |
|------------|------|-----------|
| **SmartAIRouter** | `ai/router.py` | `intent_only_classifier`, `context_planner`, `ai_router_pro_v2`, `get_plan_v7`, `get_plan_v7_light` |
| **ContextManager** | `ai_engine.py` | `build_context`, `_build_arc_scope_context`, `get_chunks_for_chapters`, `llm_select_chunks_for_query` |
| **Intent Handlers** | `ai_engine.py` | `_intent_handle_*` (clarification, template, llm_casual, data_operation, llm_with_context) |
| **Executor V7** | `core/executor_v7.py` | `execute_plan`, `step_to_router_result`, re-planning |
| **AIService** | `ai/service.py` | `call_openrouter`, `get_embedding`, `estimate_tokens` |
| **Data Operations** | `core/data_operation_jobs.py` | `run_data_operation`, unified chapter analyze |

### 1.3 Các điểm gọi LLM hiện tại (OpenRouter)

| Vị trí | Mục đích |
|--------|----------|
| `ai/router.py` | Intent classifier, Context planner, Router V2, Planner V7, Planner V7 light |
| `ai_engine.py` | LLM chọn chunks, build context |
| `ai/evaluate.py` | Đánh giá outcome, re-plan |
| `ai/content.py` | Suggest relations, arc summary, chapter metadata, extract timeline, split logic |
| `ai/rule_mining.py` | Rule mining |
| `core/executor_v7.py` | Trả lời từng step |
| `core/unified_chapter_analyze.py` | Phân tích unified theo chương |
| `core/chapter_logic_check.py` | Soát lỗi logic |
| `views/chat.py` | Draft, numerical code gen, verification, response chính |
| `views/data_analyze.py` | Data analyze |
| `views/bible.py` | Bible extraction |
| `ai_verifier.py` | Verification loop |

### 1.4 Intent và Handler mapping

```
ask_user_clarification → clarification
suggest_v7            → template
web_search            → llm_casual
chat_casual           → llm_casual
search_context        → llm_with_context
query_Sql             → llm_with_context
numerical_calculation → llm_with_context
check_chapter_logic   → llm_with_context
multi_chapter_analysis→ llm_with_context
analyze_pacing        → llm_with_context
unified               → data_operation
```

---

## 2. Phân tích điểm yếu và cơ hội

### 2.1 Điểm yếu hiện tại

1. **Nhiều lần gọi LLM tuần tự:** Intent → Planner → Context → Response. Mỗi bước là 1 request riêng, tăng latency và chi phí.
2. **Không có tool calling native:** LLM chỉ nhận prompt + context, không "gọi" trực tiếp các hàm như `search_bible`, `get_timeline`, `run_unified`.
3. **Luồng cứng nhắc:** Router → Planner → Handler là pipeline cố định, khó mở rộng hoặc thêm nhánh điều kiện.
4. **Re-planning thủ công:** Executor V7 phải parse JSON plan, gọi từng step, đánh giá outcome, gọi LLM lại để re-plan.
5. **Thiếu state persistence:** Không có cơ chế durable execution; lỗi giữa chừng phải bắt đầu lại.

### 2.2 Cơ hội khi nâng cấp

1. **LangGraph:** Định nghĩa graph (nodes + edges), state chung, conditional routing, human-in-the-loop, durable execution.
2. **DeepSeek Function Calling:** LLM tự quyết định gọi tool nào, với tham số chuẩn JSON Schema. Giảm số lần "prompt → parse → gọi code" thủ công.
3. **Gộp Intent + Planner + Tool vào 1 agent:** Một node LangGraph có thể dùng LLM + tools; LLM gọi `get_context`, `run_unified`, `web_search` trực tiếp.
4. **Streaming và observability:** LangGraph tích hợp LangSmith để trace, debug, đánh giá.

---

## 3. LangGraph và DeepSeek AI Native Tools

### 3.1 LangGraph

- **Định nghĩa:** Framework orchestration cấp thấp cho agent stateful, multi-step.
- **Cấu trúc:** Graph = Nodes (agents/steps) + Edges (routing) + State (shared).
- **Lợi ích:** Durable execution, human-in-the-loop, conditional routing, cyclic workflows.
- **Cài đặt:** `pip install -U langgraph langchain langchain-openai`

**Ví dụ cơ bản:**
```python
from langgraph.graph import StateGraph, MessagesState, START, END

def agent_node(state: MessagesState):
    # LLM + tools
    return {"messages": [...]}

graph = StateGraph(MessagesState)
graph.add_node("agent", agent_node)
graph.add_edge(START, "agent")
graph.add_conditional_edges("agent", should_continue)
graph.add_edge("agent", END)
app = graph.compile()
```

### 3.2 DeepSeek Function Calling (AI Native Tools)

- **Định nghĩa:** Cho phép model gọi external tools thông qua structured output.
- **Luồng:** User query → Model trả `tool_calls` → App thực thi function → Trả kết quả → Model trả response cuối.
- **API:** Tương thích OpenAI format. `base_url="https://api.deepseek.com"`, `model="deepseek-chat"` hoặc `deepseek-reasoner`.
- **Strict mode (Beta):** `strict: true` trong tool definition, `base_url="https://api.deepseek.com/beta"` để đảm bảo output đúng JSON Schema.

**Ví dụ tool definition:**
```python
tools = [
    {
        "type": "function",
        "function": {
            "name": "search_bible",
            "description": "Tìm thông tin nhân vật, địa điểm trong Bible theo tên entity.",
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Danh sách tên entity cần tra cứu"
                    },
                    "project_id": {"type": "string", "description": "ID dự án"}
                },
                "required": ["entity_names", "project_id"],
                "additionalProperties": False
            }
        }
    }
]
```

---

## 4. Kiến trúc mục tiêu

### 4.1 Tổng quan

```
                    ┌─────────────────────────────────────────────────────────┐
                    │                    LangGraph App                         │
                    │  ┌─────────┐    ┌─────────────┐    ┌─────────────────┐  │
User Input ───────►│  │ Router  │───►│ Main Agent  │───►│ Response        │  │──► Output
                    │  │ Node    │    │ (LLM+Tools) │    │ Formatter       │  │
                    │  └─────────┘    └──────┬──────┘    └─────────────────┘  │
                    │         │               │                    ▲           │
                    │         │               │  tool_calls        │           │
                    │         │               ▼                    │           │
                    │         │        ┌──────────────┐     ┌──────┴──────┐   │
                    │         │        │ Tool Executor│────►│ Tool Results│   │
                    │         │        │ (Python)     │     │ → messages  │   │
                    │         │        └──────────────┘     └─────────────┘   │
                    │         │               ▲                               │
                    │         │               │                               │
                    │         └───────────────┴───────────────────────────────┤
                    │                    Shared State                          │
                    └─────────────────────────────────────────────────────────┘
```

### 4.2 State Schema (LangGraph)

```python
from typing import TypedDict, Annotated, List
from langgraph.graph.message import add_messages

class VUniversalState(TypedDict):
    messages: Annotated[List, add_messages]
    project_id: str
    user_id: str
    persona: dict
    router_result: dict | None      # intent, context_needs, chapter_range, ...
    context_text: str               # Đã gather từ tools
    current_step: int
    plan: List[dict]
    strict_mode: bool
    metadata: dict
```

### 4.3 Các Node chính

| Node | Chức năng |
|------|-----------|
| **router** | Phân loại intent nhanh (có thể dùng LLM nhẹ hoặc rule-based). Cập nhật `router_result` vào state. |
| **main_agent** | LLM (DeepSeek) + Tools. Gọi tools để lấy context, thực thi data op, web search. Trả về messages có thể chứa tool_calls. |
| **tool_executor** | Nhận tool_calls, map tới Python functions, thực thi, trả tool results. |
| **response_formatter** | Chuẩn hóa output cuối (streaming, markdown, sources). |

### 4.4 Conditional Edges

- Sau `main_agent`: nếu có `tool_calls` → `tool_executor` → quay lại `main_agent`; nếu không → `response_formatter` → END.
- Sau `router`: có thể nhánh `unified` → job nền, `chat_casual` → bỏ qua context tools.

---

## 5. Kế hoạch triển khai chi tiết

### Phase 1: Chuẩn bị hạ tầng (2–3 tuần)

#### 1.1 Cài đặt và cấu hình

- [ ] Thêm `langgraph`, `langchain`, `langchain-openai` vào `requirements.txt`.
- [ ] Tạo `ai/langgraph_app.py` – module LangGraph chính.
- [ ] Tạo `ai/deepseek_tools.py` – định nghĩa tools và wrapper gọi DeepSeek API với `tools` parameter.
- [ ] Cấu hình `DEEPSEEK_API_KEY` trong secrets (hoặc dùng OpenRouter với model DeepSeek nếu cần).

#### 1.2 Adapter AIService cho DeepSeek

- [ ] Thêm `AIService.call_deepseek_with_tools(messages, tools, model="deepseek-chat")` trong `ai/service.py`.
- [ ] Hỗ trợ `tool_calls` và `tool` message format (OpenAI-compatible).
- [ ] Xử lý loop: LLM trả tool_calls → execute → append tool result → gọi lại LLM cho đến khi không còn tool_calls.

### Phase 2: Định nghĩa Tools (2–3 tuần)

#### 2.1 Tools cần thiết

| Tool | Mô tả | Tham số chính | Map từ |
|------|-------|---------------|--------|
| `search_bible` | Tra cứu Bible theo entity | `entity_names`, `project_id`, `chapter_range?` | `get_bible_entries`, `get_bible_index` |
| `search_relations` | Lấy quan hệ giữa entities | `entity_names`, `project_id` | `get_entity_relations`, `get_top_relations_by_query` |
| `search_timeline` | Lấy sự kiện timeline | `keywords`, `project_id`, `chapter_range?` | `get_timeline_events`, `get_top_timeline_by_query` |
| `search_chunks` | Semantic search chunks | `query`, `project_id`, `chapter_range?`, `max_results` | `search_chunks_vector`, `HybridSearch` |
| `get_chapter_summary` | Tóm tắt chương | `chapter_numbers`, `project_id` | `ContextManager.get_chunks_for_chapters`, metadata |
| `web_search` | Tìm kiếm web | `query`, `max_results` | `utils.web_search.web_search` |
| `run_unified_analyze` | Chạy unified (Bible+Timeline+Chunks+Relations) | `project_id`, `chapter_start`, `chapter_end` | `run_data_operation`, `unified_chapter_analyze` |
| `check_chapter_logic` | Soát lỗi logic chương | `project_id`, `chapter_number` | `run_chapter_logic_check` |
| `get_project_overview` | Tổng quan dự án (arcs, chapters, bible index) | `project_id` | `get_project_overview` |
| `get_mandatory_rules` | Lấy quy tắc dự án | `project_id` | `get_mandatory_rules` |

#### 2.2 JSON Schema cho từng tool

- [ ] Viết schema đầy đủ (properties, required, description) cho từng tool.
- [ ] Cân nhắc dùng `strict: true` (DeepSeek Beta) cho các tool quan trọng.
- [ ] Đảm bảo `project_id` được inject từ state, không để LLM tự bịa.

### Phase 3: LangGraph App cơ bản (3–4 tuần)

#### 3.1 Graph đơn giản (Single-Agent + Tools)

- [ ] Tạo `StateGraph` với `VUniversalState`.
- [ ] Node `main_agent`: bind LLM (DeepSeek) + tools. Gọi `call_deepseek_with_tools`.
- [ ] Node `tool_executor`: map `tool_calls` → Python functions, append tool results.
- [ ] Conditional edge: có tool_calls → tool_executor → main_agent; không → END.
- [ ] Test với câu hỏi đơn giản: "Võ Quốc Thanh là ai?", "Quan hệ A và B?"

#### 3.2 Tích hợp Router

- [ ] Thêm node `router`: gọi `SmartAIRouter.intent_only_classifier` (giữ logic cũ) hoặc rule-based nhanh.
- [ ] Cập nhật state `router_result`.
- [ ] Có thể bỏ qua tools cho `chat_casual`, `ask_user_clarification`; chỉ inject tools cho `search_context`, `unified`, v.v.

#### 3.3 Tích hợp Persona và Rules

- [ ] Inject `persona`, `get_mandatory_rules` vào system message của main_agent.
- [ ] Giữ `strict_mode` từ settings để điều chỉnh prompt.

### Phase 4: Thay thế luồng Chat (4–5 tuần)

#### 4.1 Tích hợp vào `views/chat.py`

- [ ] Thêm feature flag `use_langgraph` (Settings hoặc env).
- [ ] Khi `use_langgraph=True`: gọi `langgraph_app.invoke(state)` thay vì luồng cũ.
- [ ] Chuyển đổi `chat_history` → `messages` format LangGraph.
- [ ] Chuyển đổi output LangGraph → format hiển thị chat (streaming nếu có).

#### 4.2 Xử lý V7 Planner

- [ ] Option A: Planner thành 1 node riêng, sinh `plan` → Executor chạy từng step với tools.
- [ ] Option B: Main agent tự gọi tools theo thứ tự (search_bible → search_relations → search_chunks) trong 1 conversation, không cần plan tường minh.
- [ ] Ưu tiên Option B trước để đơn giản; Option A khi cần multi-step phức tạp (vd. "tóm tắt chương 1 rồi so sánh timeline chương 2").

#### 4.3 Data Operations (unified)

- [ ] Tool `run_unified_analyze` tạo background job, trả message "Đang chạy ngầm...".
- [ ] Giữ logic `_start_data_operation_background` cho UX (toast, rerun).
- [ ] Có thể thêm tool `get_job_status` để agent hỏi trạng thái job.

### Phase 5: Tối ưu và mở rộng (2–3 tuần)

#### 5.1 Streaming

- [ ] LangGraph hỗ trợ `stream()` – stream từng event (message, tool_call, tool_result).
- [ ] Tích hợp với `st.write_stream` trong Streamlit.

#### 5.2 Human-in-the-loop

- [ ] Dùng `interrupt_before` cho node cần xác nhận (vd. trước khi `run_unified_analyze`).
- [ ] User xác nhận → `graph.update_state()` → tiếp tục.

#### 5.3 Durable execution (tuỳ chọn)

- [ ] LangGraph checkpointing – lưu state sau mỗi node.
- [ ] Resume khi lỗi hoặc timeout.

#### 5.4 Observability

- [ ] Bật LangSmith (`LANGSMITH_TRACING=true`) để trace từng bước.
- [ ] Log cost, latency, tool usage.

---

## 6. Định nghĩa Tools cho DeepSeek

### 6.1 Tool: search_bible

```json
{
  "type": "function",
  "function": {
    "name": "search_bible",
    "description": "Tra cứu thông tin nhân vật, địa điểm, khái niệm trong Bible của dự án. Dùng khi user hỏi về nhân vật, mô tả entity.",
    "parameters": {
      "type": "object",
      "properties": {
        "entity_names": {
          "type": "array",
          "items": {"type": "string"},
          "description": "Danh sách tên entity cần tra (vd: ['Cường', 'Võ Quốc Thanh'])"
        },
        "chapter_range": {
          "type": "array",
          "items": {"type": "integer"},
          "description": "Khoảng chương [start, end] nếu cần giới hạn. Null nếu toàn dự án."
        }
      },
      "required": ["entity_names"],
      "additionalProperties": false
    }
  }
}
```

### 6.2 Tool: search_chunks

```json
{
  "type": "function",
  "function": {
    "name": "search_chunks",
    "description": "Tìm kiếm semantic trong các chunk (đoạn văn đã tách) của dự án. Dùng khi cần chi tiết vụn: ai nói gì, hành động, sự kiện cụ thể.",
    "parameters": {
      "type": "object",
      "properties": {
        "query": {
          "type": "string",
          "description": "Câu query mô tả nội dung cần tìm (vd: 'Cường cầm vũ khí gì', 'trận chiến quan trọng')"
        },
        "chapter_range": {
          "type": "array",
          "items": {"type": "integer"},
          "description": "Khoảng chương [start, end]. Null = toàn dự án."
        },
        "max_results": {
          "type": "integer",
          "description": "Số chunk tối đa trả về",
          "default": 15
        }
      },
      "required": ["query"],
      "additionalProperties": false
    }
  }
}
```

### 6.3 Tool: run_unified_analyze

```json
{
  "type": "function",
  "function": {
    "name": "run_unified_analyze",
    "description": "Chạy phân tích unified (Bible + Timeline + Chunks + Relations) cho khoảng chương. Chỉ dùng khi user RA LỆNH rõ ràng: 'chạy unified', 'unified chương X đến Y'.",
    "parameters": {
      "type": "object",
      "properties": {
        "chapter_start": {"type": "integer", "description": "Chương bắt đầu"},
        "chapter_end": {"type": "integer", "description": "Chương kết thúc"}
      },
      "required": ["chapter_start", "chapter_end"],
      "additionalProperties": false
    }
  }
}
```

### 6.4 Tool: web_search

```json
{
  "type": "function",
  "function": {
    "name": "web_search",
    "description": "Tìm kiếm thông tin thực tế bên ngoài (tỷ giá, tin tức, thời tiết, tra cứu). Dùng khi user hỏi thông tin thời gian thực.",
    "parameters": {
      "type": "object",
      "properties": {
        "query": {"type": "string", "description": "Câu tìm kiếm"},
        "max_results": {"type": "integer", "default": 5}
      },
      "required": ["query"],
      "additionalProperties": false
    }
  }
}
```

*(Các tool còn lại: search_relations, search_timeline, get_chapter_summary, check_chapter_logic, get_project_overview, get_mandatory_rules – format tương tự.)*

---

## 7. Lộ trình và mốc thời gian

| Phase | Nội dung | Thời lượng | Phụ thuộc |
|-------|----------|------------|-----------|
| **1** | Chuẩn bị hạ tầng (LangGraph, DeepSeek adapter) | 2–3 tuần | - |
| **2** | Định nghĩa Tools (JSON Schema + Python impl) | 2–3 tuần | Phase 1 |
| **3** | LangGraph App cơ bản (single-agent + tools) | 3–4 tuần | Phase 2 |
| **4** | Thay thế luồng Chat, tích hợp V7 | 4–5 tuần | Phase 3 |
| **5** | Tối ưu, streaming, human-in-the-loop | 2–3 tuần | Phase 4 |

**Tổng ước lượng:** 13–18 tuần (khoảng 3–4.5 tháng).

### Mốc kiểm tra

- **M1:** Gọi DeepSeek với tools, nhận tool_calls, thực thi và trả kết quả.
- **M2:** LangGraph graph chạy end-to-end với 3–5 tools cơ bản.
- **M3:** Chat V Work dùng LangGraph với feature flag, so sánh kết quả với luồng cũ.
- **M4:** Tắt luồng cũ, LangGraph là mặc định.

---

## Phụ lục

### A. Tài liệu tham khảo

- [DeepSeek Function Calling](https://api-docs.deepseek.com/guides/function_calling/)
- [LangGraph Overview](https://docs.langchain.com/oss/python/langgraph)
- [LangGraph Multi-Agent Workflows](https://blog.langchain.com/langgraph-multi-agent-workflows)

### B. File cần tạo/sửa

| File | Hành động |
|------|-----------|
| `ai/langgraph_app.py` | Tạo mới – LangGraph graph |
| `ai/deepseek_tools.py` | Tạo mới – Tool definitions + executors |
| `ai/service.py` | Sửa – Thêm `call_deepseek_with_tools` |
| `views/chat.py` | Sửa – Branch `use_langgraph` |
| `config.py` | Sửa – Thêm `DEEPSEEK_API_KEY`, `USE_LANGGRAPH` |
| `requirements.txt` | Sửa – Thêm langgraph, langchain |

### C. Rủi ro và giảm thiểu

| Rủi ro | Giảm thiểu |
|--------|------------|
| DeepSeek tool calling kém ổn định | Giữ OpenRouter làm fallback; A/B test |
| Latency tăng do nhiều tool calls | Cache kết quả tools; giới hạn số vòng tool loop |
| Chi phí tăng | Ưu tiên DeepSeek (rẻ); monitor token usage |
| Breaking change cho user | Feature flag; rollout từng nhóm |
