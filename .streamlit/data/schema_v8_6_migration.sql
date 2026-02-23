-- ==============================================================================
-- V8.6 Migration: RPC bulk update embedding (Đồng bộ vector tối ưu)
-- Chạy sau schema_v8_5_migration.sql. Cần extension vector (pgvector).
-- Dùng cho: run_embedding_backfill cập nhật nhiều embedding trong 1 lần gọi thay vì N update.
-- updates = [ {"id": "uuid", "embedding": [float, ...]}, ... ]
-- ==============================================================================

-- 1) story_bible
CREATE OR REPLACE FUNCTION bulk_update_story_bible_embeddings(updates jsonb)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
  elem jsonb;
  v_text text;
BEGIN
  FOR elem IN SELECT * FROM jsonb_array_elements(updates)
  LOOP
    IF elem->>'id' IS NOT NULL AND elem->'embedding' IS NOT NULL AND jsonb_array_length(elem->'embedding') > 0 THEN
      v_text := (elem->'embedding')::text;
      UPDATE story_bible SET embedding = v_text::vector WHERE id = (elem->>'id')::uuid;
    END IF;
  END LOOP;
END;
$$;

COMMENT ON FUNCTION bulk_update_story_bible_embeddings(jsonb) IS 'V8.6: Cập nhật embedding cho nhiều story_bible trong một lần (Đồng bộ vector).';

-- 2) chunks
CREATE OR REPLACE FUNCTION bulk_update_chunks_embeddings(updates jsonb)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
  elem jsonb;
  v_text text;
BEGIN
  FOR elem IN SELECT * FROM jsonb_array_elements(updates)
  LOOP
    IF elem->>'id' IS NOT NULL AND elem->'embedding' IS NOT NULL AND jsonb_array_length(elem->'embedding') > 0 THEN
      v_text := (elem->'embedding')::text;
      UPDATE chunks SET embedding = v_text::vector WHERE id = (elem->>'id')::uuid;
    END IF;
  END LOOP;
END;
$$;

COMMENT ON FUNCTION bulk_update_chunks_embeddings(jsonb) IS 'V8.6: Cập nhật embedding cho nhiều chunks trong một lần (Đồng bộ vector).';

-- 3) entity_relations
-- Dùng id::text = elem->>'id' để khớp cả id UUID và id BIGINT (một số DB cũ dùng bigint cho entity_relations.id).
CREATE OR REPLACE FUNCTION bulk_update_entity_relations_embeddings(updates jsonb)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
  elem jsonb;
  v_text text;
BEGIN
  FOR elem IN SELECT * FROM jsonb_array_elements(updates)
  LOOP
    IF elem->>'id' IS NOT NULL AND elem->'embedding' IS NOT NULL AND jsonb_array_length(elem->'embedding') > 0 THEN
      v_text := (elem->'embedding')::text;
      UPDATE entity_relations SET embedding = v_text::vector WHERE id::text = (elem->>'id');
    END IF;
  END LOOP;
END;
$$;

COMMENT ON FUNCTION bulk_update_entity_relations_embeddings(jsonb) IS 'V8.6: Cập nhật embedding cho nhiều entity_relations. WHERE id::text để hỗ trợ cả id UUID và BIGINT.';

-- 4) timeline_events
-- timeline_events.id là UUID (schema V7); vẫn dùng id::text để tránh lỗi cast khi client gửi id dạng khác.
CREATE OR REPLACE FUNCTION bulk_update_timeline_events_embeddings(updates jsonb)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
  elem jsonb;
  v_text text;
BEGIN
  FOR elem IN SELECT * FROM jsonb_array_elements(updates)
  LOOP
    IF elem->>'id' IS NOT NULL AND elem->'embedding' IS NOT NULL AND jsonb_array_length(elem->'embedding') > 0 THEN
      v_text := (elem->'embedding')::text;
      UPDATE timeline_events SET embedding = v_text::vector WHERE id::text = (elem->>'id');
    END IF;
  END LOOP;
END;
$$;

COMMENT ON FUNCTION bulk_update_timeline_events_embeddings(jsonb) IS 'V8.6: Cập nhật embedding cho nhiều timeline_events trong một lần (Đồng bộ vector).';
