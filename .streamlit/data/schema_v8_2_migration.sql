-- ==============================================================================
-- V8.2 Migration: parent_event_id (timeline), parent_chunk_id (chunks) — đồng bộ đi theo Bible chuẩn
-- Chạy sau schema_v8_migration.sql.
-- Dùng cho global_data_sync: timeline/chunk có thể trỏ về bản "gốc" (cùng sự kiện / cùng đoạn) ở chương trước.
-- ==============================================================================

-- ------------------------------------------------------------------------------
-- 1) timeline_events: parent_event_id — sự kiện ở chương sau là "con" của sự kiện cùng ý ở chương trước
-- ------------------------------------------------------------------------------
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'timeline_events' AND column_name = 'parent_event_id') THEN
    ALTER TABLE timeline_events ADD COLUMN parent_event_id UUID NULL REFERENCES timeline_events(id) ON DELETE SET NULL;
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_timeline_events_parent_event ON timeline_events(parent_event_id) WHERE parent_event_id IS NOT NULL;
COMMENT ON COLUMN timeline_events.parent_event_id IS 'V8.2: Sự kiện gốc (chương trước) khi sự kiện này là bản cùng ý ở chương sau. Dùng cho đồng bộ toàn cục.';

-- ------------------------------------------------------------------------------
-- 2) chunks: parent_chunk_id — chunk ở chương sau có thể trỏ về chunk "gốc" (cùng cảnh/tiếp nối) ở chương trước
-- ------------------------------------------------------------------------------
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'chunks' AND column_name = 'parent_chunk_id') THEN
    ALTER TABLE chunks ADD COLUMN parent_chunk_id UUID NULL REFERENCES chunks(id) ON DELETE SET NULL;
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_chunks_parent_chunk ON chunks(parent_chunk_id) WHERE parent_chunk_id IS NOT NULL;
COMMENT ON COLUMN chunks.parent_chunk_id IS 'V8.2: Chunk gốc (chương trước) khi chunk này cùng cảnh/tiếp nối. Dùng cho đồng bộ toàn cục.';
