# Kiểm tra embedding Timeline & Relation (chỉ báo cáo, không sửa)

**Vị trí logic:** `core/background_jobs.py` — `run_embedding_backfill` (Relations: ~579–609, Timeline: ~610–625).

## Relation embedding

- **Cách làm:** Lấy relation có `embedding` NULL → resolve `source_entity_id`/`target_entity_id` sang tên từ `story_bible` → text = `"{src} {rtype} {tgt} {desc}"` → gọi embedding batch → update DB.
- **Có thể gây lỗi:**
  1. **Bible đã xóa:** Nếu `source_entity_id` hoặc `target_entity_id` không còn trong `story_bible`, `id_to_name.get(..., "")` trả về `""` → text có thể chỉ còn "  relation_type   desc" (vẫn embed được nhưng kém nghĩa).
  2. **Kiểu `entity_relations.id`:** Đã sửa: RPC V8.6 dùng `WHERE id::text = (elem->>'id')` nên hỗ trợ cả id UUID và BIGINT. Fallback gửi embedding dạng chuỗi `"[0.1,0.2,...]"` và log lỗi nếu update thất bại.
  3. **Thứ tự `response.data`:** `get_embeddings_batch` trả về list cùng thứ tự với `texts_rel`; index khớp với `rows_rel` — ổn.

## Timeline embedding

- **Cách làm:** Lấy timeline_events có `embedding` NULL → text = `"{title} {desc} {raw_date}"` → embedding batch → update DB.
- **Có thể gây lỗi:**
  1. **Kiểu `timeline_events.id`:** Đã sửa: RPC dùng `WHERE id::text = (elem->>'id')`; fallback gửi embedding dạng chuỗi và log lỗi.
  2. **Text rỗng:** Nếu title/description/raw_date đều trống, text = ""; `get_embeddings_batch` bỏ qua (valid_indices) nên vector tại index đó là None → payloads bỏ qua → không update dòng đó (hợp lý).

## Kết luận

- Đã chỉnh RPC (id::text) và fallback (embedding chuỗi + log). Nếu “embedding Timeline/Relation không làm” hoặc lỗi khi đồng bộ, nên kiểm tra:
  Đã sửa RPC/fallback (xem trên). Nếu vẫn không lưu: xem log RPC/fallback, kiểm tra đã chạy v8.4 + v8.6. Quan hệ trỏ tới bible còn tồn tại thì text embed đầy đủ hơn.
