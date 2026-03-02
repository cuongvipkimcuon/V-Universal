# Kế hoạch nâng cấp V10 — Bản thiết kế Đại Chỉnh Sửa

> **Phiên bản:** 1.0  
> **Ngày:** 02/03/2025  
> **Mục tiêu:** Thống nhất kiến trúc dữ liệu (Data Schema) và luồng trả lời câu hỏi (Retrieval Flow) theo **Bản thiết kế Đại Chỉnh Sửa** — Custom GraphRAG với mỏ neo ID cứng, Parent-Child, và Ngũ Hổ Tướng.

---

## Mục lục

1. [Tổng quan và triết lý](#1-tổng-quan-và-triết-lý)
2. [Phần 1: Kiến trúc dữ liệu (Data Schema)](#2-phần-1-kiến-trúc-dữ-liệu-data-schema)
3. [Phần 2: Luồng truy vấn (Retrieval Flow)](#3-phần-2-luồng-truy-vấn-retrieval-flow)
4. [Phần 3: Luồng cập nhật ngầm (Ingestion Flow)](#4-phần-3-luồng-cập-nhật-ngầm-ingestion-flow)
5. [So sánh hiện tại vs mục tiêu V10](#5-so-sánh-hiện-tại-vs-mục-tiêu-v10)
6. [Lộ trình triển khai](#6-lộ-trình-triển-khai)
7. [SQL Migration và file cần sửa](#7-sql-migration-và-file-cần-sửa)

---

## 1. Tổng quan và triết lý

### 1.1 Kim chỉ nam

Kiến trúc V10 biến V-Universal thành **Custom GraphRAG**:

- **Rẻ hơn:** Loại bỏ ~80% tác vụ Embedding thừa (Bible, Relation, Timeline, Rule, Chat).
- **Thông minh hơn:** Hiểu dòng thời gian qua Root/Branch (Bible), Parent-Child (Chunk).
- **Chính xác hơn:** Mọi diễn biến được mỏ neo cứng vào Bible ID; không ảo giác (hallucination) từ vector lệch.

### 1.2 Nguyên tắc cốt lõi

| Nguyên tắc | Mô tả |
|------------|-------|
| **Mỏ neo ID** | Bible Entity là gốc; Relation, Timeline, Chunk đều link cứng qua `entity_id` / `chunk_id`. |
| **Parent-Child** | Bible: Root (tóm tắt lũy tiến) + Branch (trạng thái theo chương). Chunk: Parent (đoạn lớn) + Child (câu nhỏ). |
| **Fuzzy > Vector** | Bible, Relation, Timeline: tra cứu theo ID/tên (fuzzy, ilike), không embedding. |
| **Vector chỉ cho Chunk** | Chỉ embed câu nhỏ (Child) → semantic search → bốc Parent Chunk. |

---

## 2. Phần 1: Kiến trúc dữ liệu (Data Schema)

### 2.1 Bảng và vai trò

| Bảng | Vai trò & cấu trúc | Phương thức truy xuất | Embedding |
|------|--------------------|------------------------|-----------|
| **story_bible** | **[Gốc]** Root (tóm tắt lũy tiến) & Branch (trạng thái theo chương). Mỏ neo ID cho toàn hệ thống. | **Fuzzy Search** (pg_trgm / ilike) trên `entity_name`. | ❌ **BỎ** |
| **entity_relations** | Liên kết cứng giữa `source_entity_id` ↔ `target_entity_id`. Bối cảnh quan hệ theo chương. | Dò theo **Entity_ID** của Root Bible. | ❌ **BỎ** |
| **timeline_events** | Liên kết cứng với **Entity_ID** (ai làm) và **Chunk_ID** (đoạn văn bằng chứng). | Dò theo **Entity_ID** hoặc **Chunk_ID**. | ❌ **BỎ** |
| **chunks** | **[Gốc]** Parent Chunk (đoạn lớn) chứa nhiều Child Sentences (câu nhỏ). | **Semantic Search** trên Child → bốc Parent. | ✅ **GIỮ** (chỉ embed câu nhỏ) |
| **rules, chat_crystallize** | Quy tắc viết và lịch sử hội thoại kết tinh. | Lọc Metadata (Scope, Type, Session_ID). | ❌ **BỎ** |

### 2.2 Chi tiết schema cần thay đổi

#### 2.2.1 story_bible — Root & Branch

**Hiện tại:** Đã có `parent_id` (V5). Thiếu `node_type` để phân biệt Root vs Branch.

**V10 cần thêm:**

```sql
-- node_type: 'root' | 'branch'
-- root: Tóm tắt lũy tiến (tổng hợp toàn bộ đã biết)
-- branch: Trạng thái mới/đổi ở chương cụ thể (source_chapter)
ALTER TABLE story_bible ADD COLUMN IF NOT EXISTS node_type TEXT DEFAULT 'root'
  CHECK (node_type IN ('root', 'branch'));
```

**Quy ước:**

- Entity mới → tạo **Root** với `node_type='root'`, `parent_id=NULL`.
- Khi có thông tin mới ở chương X → tạo **Branch** với `node_type='branch'`, `parent_id=root_id`, `source_chapter=X`.
- Root được cập nhật "tóm tắt lũy tiến" sau mỗi lần ingest (xem Phần 3).

#### 2.2.2 chunks — Parent & Child (câu nhỏ)

**Hiện tại:** Chunk = đoạn văn (content). Có `parent_chunk_id` (V8.2) cho chunk tiếp nối chương sau. **Chưa có** bảng câu nhỏ (Child).

**V10 có 2 hướng:**

**Option A (nhẹ):** Giữ nguyên `chunks`; embed `content` hoặc `meta_json.chunk_summary` như hiện tại. Chỉ bỏ embedding ở Bible/Relation/Timeline.

**Option B (đầy đủ):** Thêm bảng `chunk_sentences`:

```sql
CREATE TABLE IF NOT EXISTS chunk_sentences (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  chunk_id UUID NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
  story_id UUID NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
  sentence_text TEXT NOT NULL,
  sort_order INT NOT NULL DEFAULT 0,
  embedding vector(4096),  -- CHỈ bảng này có embedding
  created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_chunk_sentences_chunk ON chunk_sentences(chunk_id);
CREATE INDEX IF NOT EXISTS idx_chunk_sentences_embedding ON chunk_sentences 
  USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
```

- Semantic search trên `chunk_sentences` → lấy `chunk_id` → bốc Parent `chunks`.

**Khuyến nghị:** Bắt đầu với **Option A** (ít thay đổi). Option B triển khai khi cần độ chính xác cao hơn.

#### 2.2.3 Bỏ embedding ở Bible, Relation, Timeline, Rules, Chat

- `story_bible`: DROP cột `embedding` (sau khi chuyển retrieval sang fuzzy).
- `entity_relations`: DROP cột `embedding`.
- `timeline_events`: DROP cột `embedding`.
- `rules` / `chat_crystallize_entries`: Bỏ embedding; lọc theo metadata.

**Lưu ý:** Migration phải chạy **sau** khi code retrieval đã chuyển sang dùng fuzzy/ID. Tránh break production.

---

## 3. Phần 2: Luồng truy vấn (Retrieval Flow)

### 3.1 Tổng quan — "Kẻ Lập Kế Hoạch"

Khi user đặt câu hỏi, hệ thống chạy **4 bước** không thể sai lệch:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  Bước 1: MASTER PLANNER (LLM Gemini Flash)                                   │
│  Nhận câu hỏi → Phân tích → Xuất JSON danh sách Tools cần gọi.               │
│  Không tự đi tìm; chỉ "chỉ tay năm ngón".                                    │
└─────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  Bước 2: NGŨ HỔ TƯỚNG (Parallel Tools)                                      │
│  Backend nhận JSON → Chạy song song (Promise.all / asyncio.gather):         │
│  1. GET_ENTITY_PROFILE   — Fuzzy search Entity → Relation + Timeline        │
│  2. SEMANTIC_CHUNK_SEARCH — Vector search Child → Parent Chunk              │
│  3. GET_RELATED_CHUNKS   — Chunk có tag Entity đang hỏi                      │
│  4. GET_STORY_MAP        — Arc/Chương tóm tắt (bối cảnh vĩ mô)               │
│  5. WEB_KNOWLEDGE        — Tra cứu thực tế (nếu cần)                         │
└─────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  Bước 3: GỘP & KHỬ TRÙNG (Merge & Deduplication)                             │
│  Gom dữ liệu trả về → Dedupe theo Entity_ID, Parent_Chunk_ID                 │
│  → Đóng gói Markdown siêu sạch                                               │
└─────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  Bước 4: ÉP NÉN & TRÍCH XUẤT (Final Generation)                             │
│  Ném Markdown + câu hỏi → LLM cuối → Trả lời chính xác, không ảo giác       │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 3.2 Định nghĩa Ngũ Hổ Tướng (5 Tools)

| Tool | Mô tả | Input chính | Output | Map từ code hiện tại |
|------|-------|-------------|--------|---------------------|
| **GET_ENTITY_PROFILE** | Fuzzy search Entity theo tên → lấy Root ID → kéo Relation + Timeline gắn ID đó | `entity_names`, `project_id`, `chapter_range?` | Bible entries + Relations + Timeline events | `HybridSearch.smart_search_hybrid_raw`, `get_entity_relations`, `get_timeline_events` |
| **SEMANTIC_CHUNK_SEARCH** | Vector search tìm tình tiết ẩn → bốc Parent Chunk | `query`, `project_id`, `chapter_range?`, `top_k` | Chunks (content + meta) | `search_chunks_vector`, `hybrid_chunk_search` RPC |
| **GET_RELATED_CHUNKS** | Chunk có tag Entity (chunk_bible_links, meta_json.chunk_entities) | `entity_ids`, `project_id`, `chapter_range?` | Chunks | `get_chunks_for_bible_entities`, `get_event_action_chunks_for_characters` |
| **GET_STORY_MAP** | Arc/Chương tóm tắt (bối cảnh vĩ mô) | `project_id`, `arc_id?`, `chapter_numbers?` | Arc summaries, chapter summaries | `ContextManager._build_arc_scope_context`, `get_chapter_list_for_router` |
| **WEB_KNOWLEDGE** | Tra cứu thực tế (tỷ giá, tin tức...) | `query`, `max_results` | Search results text | `utils.web_search.web_search` |

### 3.3 Master Planner — Format JSON output

```json
{
  "intent": "search_context",
  "tools_to_call": [
    {"tool": "GET_ENTITY_PROFILE", "params": {"entity_names": ["Cường", "Võ Quốc Thanh"], "chapter_range": [1, 30]}},
    {"tool": "SEMANTIC_CHUNK_SEARCH", "params": {"query": "trận chiến quan trọng của Cường", "chapter_range": [1, 30], "top_k": 15}},
    {"tool": "GET_RELATED_CHUNKS", "params": {"entity_names": ["Cường"], "chapter_range": [1, 30]}},
    {"tool": "GET_STORY_MAP", "params": {"chapter_range": [1, 30]}}
  ]
}
```

- `intent`: `search_context` | `web_search` | `chat_casual` | `unified` | `ask_user_clarification` | ...
- `tools_to_call`: Chỉ chứa tools cần thiết; có thể rỗng cho `chat_casual`.

### 3.4 Ánh xạ với luồng hiện tại

| Thành phần hiện tại | V10 |
|---------------------|-----|
| `SmartAIRouter.intent_only_classifier` | Master Planner (Bước 1) — gộp intent + tool list |
| `SmartAIRouter.context_planner` | Bỏ — Planner chỉ xuất tools, không xuất context_needs chi tiết |
| `_intent_handle_llm_with_context` | Thay bằng gọi 5 tools song song (Bước 2) |
| `ContextManager.build_context` | Merge & Dedupe (Bước 3) + format Markdown |
| `AIService.call_openrouter` (response) | Final Generation (Bước 4) |

---

## 4. Phần 3: Luồng cập nhật ngầm (Ingestion Flow)

### 4.1 "Kẻ Đối Soát" — Giải bài toán "nhìn xuyên chương"

Khi xong 1 chương, Worker **không** nhét cả chương vào LLM lớn. Luồng mới:

**Bước 1: Viết và lọc nhanh**

- Dùng SLM nhỏ hoặc Vector Search để "nhặt" tên thực thể và khoanh vùng đoạn có biến động.
- Giảm token so với đọc full chapter.

**Bước 2: Đối soát với "Tóm tắt lũy tiến"**

- Bốc **Root Node** của từng entity (bản tóm tắt đã biết trước đó).
- LLM chỉ so sánh: *Bản tóm tắt cũ* vs *Nội dung chương mới*.

**Bước 3: Cập nhật cây phả hệ**

- Có thông tin mới/đổi: Tạo **Branch Node** (trạng thái ở chương này) + cập nhật **Root** (tóm tắt lũy tiến).
- Hoàn toàn mới: Tạo Root Node mới.

### 4.2 Ánh xạ với code hiện tại

| Thành phần hiện tại | V10 |
|---------------------|-----|
| `core/unified_chapter_analyze.py` | Giữ; bổ sung logic Root/Branch khi extract Bible |
| `core/data_operation_jobs.py` | Giữ; khi extract Bible tạo/update Root, Branch |
| `run_logic_check_then_save_bible` | Cập nhật để ghi `node_type`, `parent_id` đúng |

---

## 5. So sánh hiện tại vs mục tiêu V10

### 5.1 Embedding

| Bảng | Hiện tại | V10 |
|------|----------|-----|
| story_bible | ✅ embedding (hybrid_search RPC) | ❌ Bỏ; dùng ilike |
| entity_relations | ✅ embedding | ❌ Bỏ; dùng entity_id |
| timeline_events | ✅ embedding | ❌ Bỏ; dùng entity_id, chunk_id |
| chunks | ✅ embedding (hybrid_chunk_search) | ✅ Giữ (hoặc chuyển sang chunk_sentences) |
| rules | ✅ embedding | ❌ Bỏ; lọc metadata |
| chat_crystallize | ✅ embedding | ❌ Bỏ; lọc metadata |
| semantic_intent | ✅ embedding | Giữ (dùng cho intent matching) |

### 5.2 Retrieval Flow

| Bước | Hiện tại | V10 |
|------|----------|-----|
| 1 | Intent classifier (1 LLM) + Context planner (1 LLM) | Master Planner (1 LLM) — xuất tools |
| 2 | Intent Handler gọi tuần tự: Bible → Relation → Timeline → Chunk | 5 tools chạy **song song** |
| 3 | `_intent_handle_llm_with_context` gộp trong context_parts | Merge & Dedupe theo Entity_ID, Chunk_ID |
| 4 | LLM trả lời với context đã build | Giữ; Markdown sạch hơn |

### 5.3 Lợi ích V10

- **Giảm latency:** Tools chạy song song; bỏ 1 bước Context Planner.
- **Giảm cost:** Bỏ embedding Bible/Relation/Timeline/Rules/Chat (~80% embedding jobs).
- **Tăng độ chính xác:** Hard-link qua ID; không lệch do vector.
- **Dễ mở rộng:** Thêm tool mới chỉ cần định nghĩa trong Planner + implement hàm.

---

## 6. Lộ trình triển khai

### Phase 1: Chuẩn bị schema (1–2 tuần)

- [ ] Migration SQL: `story_bible` thêm `node_type`.
- [ ] (Tuỳ chọn) Tạo `chunk_sentences` nếu chọn Option B.
- [ ] Chưa DROP embedding; giữ tương thích ngược.

### Phase 2: Implement Ngũ Hổ Tướng (2–3 tuần)

- [ ] Tạo `ai/v10_tools.py` — 5 hàm: `get_entity_profile`, `semantic_chunk_search`, `get_related_chunks`, `get_story_map`, `web_knowledge`.
- [ ] `get_entity_profile`: Fuzzy search Bible (ilike) → Relation + Timeline theo entity_id. **Bỏ** gọi hybrid_search RPC.
- [ ] `semantic_chunk_search`: Giữ `search_chunks_vector` / `hybrid_chunk_search`.
- [ ] `get_related_chunks`: Dùng `get_chunks_for_bible_entities`, `get_event_action_chunks_for_characters`.
- [ ] `get_story_map`: Dùng `_build_arc_scope_context`, chapter summaries.
- [ ] `web_knowledge`: Dùng `utils.web_search.web_search`.

### Phase 3: Master Planner (1–2 tuần)

- [ ] Tạo `ai/v10_planner.py` — LLM (Gemini Flash) nhận câu hỏi, trả JSON `tools_to_call`.
- [ ] Prompt rõ ràng: danh sách 5 tools, khi nào gọi tool nào.
- [ ] Parse JSON → gọi 5 tools **song song** (asyncio hoặc ThreadPoolExecutor).

### Phase 4: Merge & Dedupe (1 tuần)

- [ ] Tạo `ai/v10_merge.py` — gom kết quả 5 tools, dedupe theo `entity_id`, `chunk_id`.
- [ ] Format Markdown chuẩn cho Final Generation.

### Phase 5: Tích hợp vào Chat (2 tuần)

- [ ] Feature flag `use_v10_retrieval` trong Settings.
- [ ] Khi bật: Chat gọi Master Planner → Tools → Merge → Final LLM.
- [ ] Khi tắt: Giữ luồng cũ (ContextManager.build_context).

### Phase 6: Bỏ embedding cũ (1–2 tuần)

- [ ] Sau khi V10 ổn định: Migration DROP embedding ở story_bible, entity_relations, timeline_events.
- [ ] Xoá/đơn giản hoá `run_embedding_backfill` cho Bible, Relation, Timeline.
- [ ] Cập nhật UI (Bible, Relations, Timeline tabs): bỏ nút "Đồng bộ vector".

### Phase 7: Ingest Root/Branch (2–3 tuần)

- [ ] Cập nhật `unified_chapter_analyze` để tạo Branch khi extract Bible.
- [ ] Cập nhật Root "tóm tắt lũy tiến" sau mỗi chapter.

---

## 7. SQL Migration và file cần sửa

### 7.1 Migration SQL (tạo mới)

**File:** `.streamlit/data/schema_v10_migration.sql`

```sql
-- V10 Migration: node_type cho story_bible (Root/Branch)
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'story_bible' AND column_name = 'node_type') THEN
    ALTER TABLE story_bible ADD COLUMN node_type TEXT DEFAULT 'root'
      CHECK (node_type IN ('root', 'branch'));
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_story_bible_node_type ON story_bible(story_id, node_type);
COMMENT ON COLUMN story_bible.node_type IS 'V10: root = tóm tắt lũy tiến, branch = trạng thái theo chương.';
```

### 7.2 File cần tạo

| File | Mô tả |
|------|-------|
| `ai/v10_tools.py` | 5 tools: get_entity_profile, semantic_chunk_search, get_related_chunks, get_story_map, web_knowledge |
| `ai/v10_planner.py` | Master Planner — LLM trả JSON tools_to_call |
| `ai/v10_merge.py` | Merge & Dedupe kết quả tools |
| `.streamlit/data/schema_v10_migration.sql` | Migration node_type |

### 7.3 File cần sửa

| File | Thay đổi |
|------|----------|
| `views/chat.py` | Branch `use_v10_retrieval` → gọi v10_planner + v10_tools + v10_merge |
| `ai/hybrid_search.py` | `get_entity_profile` dùng ilike; bỏ fallback hybrid_search cho Bible khi V10 |
| `core/unified_chapter_analyze.py` | Tạo Branch, cập nhật Root khi extract Bible |
| `core/data_operation_jobs.py` | Tương thích node_type |
| `views/bible.py`, `views/relations_view.py`, `views/timeline_view.py` | Sau Phase 6: bỏ nút Đồng bộ vector |
| `core/background_jobs.py` | Bỏ/sửa `run_embedding_backfill` cho Bible, Relation, Timeline |

---

## Phụ lục: Tương thích với LangGraph / DeepSeek Tools

Kế hoạch V10 **tương thích** với `UPGRADE_PLAN_LANGGRAPH_DEEPSEEK_TOOLS.md`:

- **Ngũ Hổ Tướng** = 5 tools có thể map sang DeepSeek Function Calling.
- **Master Planner** có thể là 1 node trong LangGraph; 5 tools chạy trong Tool Executor node.
- **Merge & Dedupe** = xử lý trước khi đưa vào Final Generation node.

Triển khai V10 trước (schema + retrieval) tạo nền tảng vững cho bước tiếp theo (LangGraph orchestration).
