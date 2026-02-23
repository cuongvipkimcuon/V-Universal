-- ==============================================================================
-- V8.7 Migration: embedding dimension 4096 (qwen3-embedding-8b)
-- Chạy khi dùng model embedding trả về 4096 chiều (VD: qwen/qwen3-embedding-8b).
-- Lỗi "expected 1536 dimensions, not 4096" → chạy migration này.
-- Cột embedding cũ (1536) sẽ bị xóa và tạo lại 4096; sau đó bấm lại "Đồng bộ vector".
-- ==============================================================================

-- entity_relations: cột embedding → vector(4096)
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'entity_relations' AND column_name = 'embedding') THEN
    ALTER TABLE entity_relations DROP COLUMN embedding;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'entity_relations' AND column_name = 'embedding') THEN
    ALTER TABLE entity_relations ADD COLUMN embedding vector(4096);
  END IF;
END $$;

-- timeline_events: cột embedding → vector(4096)
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'timeline_events' AND column_name = 'embedding') THEN
    ALTER TABLE timeline_events DROP COLUMN embedding;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'timeline_events' AND column_name = 'embedding') THEN
    ALTER TABLE timeline_events ADD COLUMN embedding vector(4096);
  END IF;
END $$;

COMMENT ON COLUMN entity_relations.embedding IS 'V8.7: vector(4096) cho qwen3-embedding-8b.';
COMMENT ON COLUMN timeline_events.embedding IS 'V8.7: vector(4096) cho qwen3-embedding-8b.';
