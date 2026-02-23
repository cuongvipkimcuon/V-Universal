# Changelog V8.5 — Tối ưu Unified chapter analyze

**Áp dụng:** Chạy migration `.streamlit/data/schema_v8_5_migration.sql` (RPC bulk update source_chunk_id).  
Nếu chưa chạy, pipeline vẫn chạy được và fallback update từng dòng cho source_chunk_id.

---

## 1. Tối ưu pipeline Unified (1 chương)

### Xóa dữ liệu cũ (bulk)
- **Bible:** Xóa relations theo `source_entity_id` / `target_entity_id` trong batch (tối đa 500 id/lần), rồi xóa `story_bible` theo `story_id` + `source_chapter` (1 lệnh).
- **Timeline:** Xóa `timeline_events` theo `story_id` + `chapter_id` (1 lệnh).
- **Chunks:** Lấy danh sách chunk id chương → xóa `chunk_bible_links` và `chunk_timeline_links` theo batch chunk_id → xóa `chunks` theo `chapter_id` (1 lệnh).

### Lưu Bible
- Load **một lần** danh sách `story_bible` (id, entity_name) của project để check trùng tên thay vì N lần.
- `validate_and_prepare_bible` nhận thêm tham số tùy chọn `existing_bible_rows` (core/user_data_save_pipeline.py).
- Insert Bible theo batch (200 bản ghi/lần).

### Lưu Timeline, Chunks, Relations, Links
- Timeline / Chunks: chuẩn bị payload rồi **batch insert** (200/lần).
- Relations: build payload từ `name_to_bible_id`, **batch insert** (không gọi validate từng relation).
- chunk_bible_links / chunk_timeline_links: build danh sách row rồi **batch insert**.

### source_chunk_id (V8.5 RPC)
- **bulk_update_bible_source_chunk(updates jsonb):** cập nhật `story_bible.source_chunk_id` cho nhiều bản ghi trong một lần.
- **bulk_update_timeline_source_chunk(updates jsonb):** cập nhật `timeline_events.source_chunk_id` cho nhiều bản ghi trong một lần.
- Nếu RPC chưa có (chưa chạy migration), code tự fallback update từng dòng như trước.

---

## 2. Database migration 8.5

- **RPC `bulk_update_bible_source_chunk(updates jsonb)`:** tham số `updates` là mảng `[{"id": "uuid", "source_chunk_id": "uuid"}, ...]`.
- **RPC `bulk_update_timeline_source_chunk(updates jsonb)`:** cùng định dạng.

**File:** `.streamlit/data/schema_v8_5_migration.sql`

---

## 3. File thay đổi

- `core/unified_chapter_analyze.py`: bulk delete, batch insert, gọi RPC v8.5 (có fallback).
- `core/user_data_save_pipeline.py`: `validate_and_prepare_bible(..., existing_bible_rows=...)`.
- `.streamlit/data/schema_v8_5_migration.sql`: định nghĩa 2 RPC.
