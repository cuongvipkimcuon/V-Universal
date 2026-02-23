-- ==============================================================================
-- V8.5 Migration: RPC bulk update source_chunk_id (Unified pipeline tối ưu)
-- Chạy sau schema_v8_4_migration.sql.
-- Dùng cho: unified_chapter_analyze cập nhật source_chunk_id Bible + Timeline trong 1 lần gọi.
-- ==============================================================================

-- 1) RPC: bulk_update_bible_source_chunk(updates jsonb)
-- updates = [ {"id": "uuid", "source_chunk_id": "uuid"}, ... ]
CREATE OR REPLACE FUNCTION bulk_update_bible_source_chunk(updates jsonb)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
  UPDATE story_bible sb
  SET source_chunk_id = v.chunk_id
  FROM (
    SELECT (elem->>'id')::uuid AS id, (elem->>'source_chunk_id')::uuid AS chunk_id
    FROM jsonb_array_elements(updates) AS elem
    WHERE elem->>'id' IS NOT NULL AND elem->>'source_chunk_id' IS NOT NULL
  ) v
  WHERE sb.id = v.id;
END;
$$;

COMMENT ON FUNCTION bulk_update_bible_source_chunk(jsonb) IS 'V8.5: Cập nhật source_chunk_id cho nhiều story_bible trong một lần (Unified pipeline).';

-- 2) RPC: bulk_update_timeline_source_chunk(updates jsonb)
-- updates = [ {"id": "uuid", "source_chunk_id": "uuid"}, ... ]
CREATE OR REPLACE FUNCTION bulk_update_timeline_source_chunk(updates jsonb)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
  UPDATE timeline_events te
  SET source_chunk_id = v.chunk_id
  FROM (
    SELECT (elem->>'id')::uuid AS id, (elem->>'source_chunk_id')::uuid AS chunk_id
    FROM jsonb_array_elements(updates) AS elem
    WHERE elem->>'id' IS NOT NULL AND elem->>'source_chunk_id' IS NOT NULL
  ) v
  WHERE te.id = v.id;
END;
$$;

COMMENT ON FUNCTION bulk_update_timeline_source_chunk(jsonb) IS 'V8.5: Cập nhật source_chunk_id cho nhiều timeline_events trong một lần (Unified pipeline).';
