-- ==============================================================================
-- V8.4 Migration: embedding cho entity_relations và timeline_events
-- Chạy sau schema_v8_3_migration.sql. Cần pgvector (vector 1536, cùng dimension với story_bible/chunks).
-- Dùng cho: đồng bộ vector từng tab (Relations, Timeline); text embed = tên + mô tả đầy đủ.
-- ==============================================================================
CREATE EXTENSION IF NOT EXISTS vector;

-- entity_relations: embedding từ (source_name + relation_type + target_name + description)
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'entity_relations' AND column_name = 'embedding') THEN
    ALTER TABLE entity_relations ADD COLUMN embedding vector(1536);
  END IF;
END $$;

-- timeline_events: embedding từ (title + description + raw_date)
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'timeline_events' AND column_name = 'embedding') THEN
    ALTER TABLE timeline_events ADD COLUMN embedding vector(1536);
  END IF;
END $$;

COMMENT ON COLUMN entity_relations.embedding IS 'V8.4: Vector từ source_name + relation_type + target_name + description. Đồng bộ bằng nút trong tab Relations.';
COMMENT ON COLUMN timeline_events.embedding IS 'V8.4: Vector từ title + description + raw_date. Đồng bộ bằng nút trong tab Timeline.';
