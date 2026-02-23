# Changelog: Database V8 — Chuẩn hóa mô hình 1-N và quan hệ parent-child

**Áp dụng sau:** `schema_v7.7_migration.sql`  
**File migration:** `.streamlit/data/schema_v8_migration.sql`

---

## 1. Mục tiêu V8

- Chuẩn hóa **mô hình 1-N** giữa **chunk**, **bible**, **timeline**, **relation** với quan hệ parent-child rõ ràng.
- Cơ sở cho **unified extract** (một chương một lần) và **chuẩn hóa dữ liệu cũ** (gán link sau).
- Bổ sung **arc** và bảng theo dõi run (chỉ schema, code thực thi tính sau).

---

## 2. Bảng mới

### 2.1. `chunk_bible_links`

| Cột | Kiểu | Mô tả |
|-----|------|--------|
| id | UUID PK | Khóa chính |
| story_id | UUID FK stories | Dự án |
| chunk_id | UUID FK chunks | Chunk (parent 1) |
| bible_entry_id | UUID FK story_bible | Bible entry (parent 1) |
| mention_role | TEXT | `primary` \| `secondary` \| `mention` \| `other` (nullable) |
| sort_order | INT | Thứ tự trong chunk |
| created_at | TIMESTAMPTZ | Thời điểm tạo |

- **Ràng buộc:** `UNIQUE(chunk_id, bible_entry_id)`.
- **Quan hệ:** Một chunk → N link (1-N Bible); một bible entry → N link (N chunk nhắc đến).
- **Index:** story_id, chunk_id, bible_entry_id.

### 2.2. `chunk_timeline_links`

| Cột | Kiểu | Mô tả |
|-----|------|--------|
| id | UUID PK | Khóa chính |
| story_id | UUID FK stories | Dự án |
| chunk_id | UUID FK chunks | Chunk (parent 1) |
| timeline_event_id | UUID FK timeline_events | Sự kiện (parent 1) |
| mention_role | TEXT | `primary` \| `secondary` \| `mention` \| `other` (nullable) |
| sort_order | INT | Thứ tự trong chunk |
| created_at | TIMESTAMPTZ | Thời điểm tạo |

- **Ràng buộc:** `UNIQUE(chunk_id, timeline_event_id)`.
- **Quan hệ:** Một chunk → N link (1-N Timeline); một timeline event → N link.
- **Index:** story_id, chunk_id, timeline_event_id.

### 2.3. `unified_extract_runs`

| Cột | Kiểu | Mô tả |
|-----|------|--------|
| id | UUID PK | Khóa chính |
| story_id | UUID FK stories | Dự án |
| chapter_id | BIGINT FK chapters | Chương được extract |
| run_at | TIMESTAMPTZ | Thời điểm chạy |
| status | TEXT | `pending` \| `running` \| `completed` \| `failed` \| `partial` |
| bible_count | INT | Số bible đã ghi |
| timeline_count | INT | Số timeline đã ghi |
| chunk_count | INT | Số chunk đã ghi |
| relation_count | INT | Số relation đã ghi |
| link_bible_added | INT | Số dòng chunk_bible_links |
| link_timeline_added | INT | Số dòng chunk_timeline_links |
| error_message | TEXT | Lỗi (nếu có) |
| meta_json | JSONB | Metadata tùy ý |
| created_at | TIMESTAMPTZ | Thời điểm tạo |

- Dùng để phân biệt dữ liệu từ **unified pipeline** với dữ liệu legacy hoặc chuẩn hóa.

### 2.4. `normalize_links_runs`

| Cột | Kiểu | Mô tả |
|-----|------|--------|
| id | UUID PK | Khóa chính |
| story_id | UUID FK stories | Dự án |
| scope_type | TEXT | `chapter` \| `project` |
| chapter_id | BIGINT FK chapters NULL | Chương (khi scope_type = chapter) |
| run_at | TIMESTAMPTZ | Thời điểm chạy |
| status | TEXT | `pending` \| `running` \| `completed` \| `failed` \| `partial` |
| chunks_processed | INT | Số chunk đã xử lý |
| link_bible_added | INT | Số dòng chunk_bible_links thêm |
| link_timeline_added | INT | Số dòng chunk_timeline_links thêm |
| error_message | TEXT | Lỗi (nếu có) |
| meta_json | JSONB | Metadata |
| created_at | TIMESTAMPTZ | Thời điểm tạo |

- Ghi mỗi lần chạy **chuẩn hóa link** trên data hiện có (không phải unified extract).

---

## 3. Cột bổ sung (ALTER)

### 3.1. `entity_relations`

| Cột mới | Kiểu | Mô tả |
|---------|------|--------|
| source_chapter | INT NULL | Chương nguồn trích xuất (parent chapter). |
| source_chunk_id | UUID NULL FK chunks | Chunk nguồn trích xuất (parent chunk). |

- Quan hệ: 1 chapter → N relations; 1 chunk → N relations (traceability).

### 3.2. `story_bible`

| Cột mới | Kiểu | Mô tả |
|---------|------|--------|
| source_chunk_id | UUID NULL FK chunks | Chunk nguồn (lần đầu xuất hiện). |

- Đã có `source_chapter` (parent chapter); `source_chunk_id` là granularity tùy chọn.

### 3.3. `timeline_events`

| Cột mới | Kiểu | Mô tả |
|---------|------|--------|
| source_chunk_id | UUID NULL FK chunks | Chunk nguồn trích xuất sự kiện. |

- Đã có `chapter_id` (parent chapter); `source_chunk_id` tùy chọn.

### 3.4. `arcs`

| Cột mới | Kiểu | Mô tả |
|---------|------|--------|
| description | TEXT | Mô tả dài (khác summary ngắn). |
| color_hex | TEXT | Màu hiển thị (hex, VD: #4A90D9). |
| display_order | INT | Thứ tự hiển thị UI (sort_order vẫn dùng logic). |

- Arc đã có `parent_arc_id`, `prev_arc_id` (parent-child); V8 chỉ bổ sung metadata hiển thị.

---

## 4. Quan hệ parent-child tóm tắt

| Bảng / Khái niệm | Parent | Child |
|-------------------|--------|--------|
| **chunks** | chapter (chapter_id), arc (arc_id) | chunk_bible_links, chunk_timeline_links |
| **story_bible** | chapter (source_chapter), chunk (source_chunk_id), parent entry (parent_id) | chunk_bible_links, entity_relations (source/target) |
| **timeline_events** | chapter (chapter_id), arc (arc_id), chunk (source_chunk_id) | chunk_timeline_links |
| **entity_relations** | chapter (source_chapter), chunk (source_chunk_id), story (story_id) | — |
| **chunk_bible_links** | chunk, bible entry | — |
| **chunk_timeline_links** | chunk, timeline event | — |
| **arcs** | story, parent_arc, prev_arc | chapters, chunks (qua chapter), timeline_events |

---

## 5. Index mới (trong migration)

- `idx_chunk_bible_links_story`, `idx_chunk_bible_links_chunk`, `idx_chunk_bible_links_bible`
- `idx_chunk_timeline_links_story`, `idx_chunk_timeline_links_chunk`, `idx_chunk_timeline_links_timeline`
- `idx_entity_relations_source_chapter` (partial: WHERE source_chapter IS NOT NULL)
- `idx_entity_relations_source_chunk` (partial: WHERE source_chunk_id IS NOT NULL)
- `idx_story_bible_source_chunk` (partial)
- `idx_timeline_events_source_chunk` (partial)
- `idx_arcs_display_order`
- `idx_unified_extract_runs_story`, `idx_unified_extract_runs_chapter`, `idx_unified_extract_runs_run_at`
- `idx_normalize_links_runs_story`, `idx_normalize_links_runs_run_at`

---

## 6. Ghi chú triển khai

- **Chỉ schema:** Migration V8 chỉ thêm bảng và cột; không chứa code ứng dụng.
- **Backward compatible:** Tất cả cột mới nullable hoặc có default; dữ liệu cũ không cần backfill ngay.
- **Unified extract:** Khi có pipeline unified, ghi đồng thời bible, timeline, chunks, relations và hai bảng link; có thể ghi thêm 1 dòng `unified_extract_runs`.
- **Chuẩn hóa:** Job chuẩn hóa đọc chunk/bible/timeline hiện có, gán link và ghi `normalize_links_runs`.
