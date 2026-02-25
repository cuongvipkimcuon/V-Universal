-- ==============================================================================
-- V9.0 Migration: Embedding cho Rules & Chat Memory + chuẩn bị quan sát LLM per-turn
-- - Thêm cột embedding (vector 4096) cho project_rules và chat_crystallize_entries.
-- - Dùng cùng dimension với qwen3-embedding-8b (4096) như chunks / relations / timeline.
-- Chạy sau schema_v8_9_migration.sql.
-- ==============================================================================

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'project_rules' AND column_name = 'embedding'
  ) THEN
    ALTER TABLE project_rules ADD COLUMN embedding vector(4096);
  END IF;
END $$;

COMMENT ON COLUMN project_rules.embedding IS 'V9.0: Embedding (4096) cho nội dung Rule, dùng để lọc trùng và search theo vector.';

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'chat_crystallize_entries' AND column_name = 'embedding'
  ) THEN
    ALTER TABLE chat_crystallize_entries ADD COLUMN embedding vector(4096);
  END IF;
END $$;

COMMENT ON COLUMN chat_crystallize_entries.embedding IS 'V9.0: Embedding (4096) cho mô tả chat crystallize, dùng để lọc trùng và search theo vector.';

-- Index optional cho similarity search (ivfflat) có thể tạo sau khi đã có đủ dữ liệu embedding:
-- CREATE INDEX IF NOT EXISTS idx_project_rules_embedding ON project_rules USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
-- CREATE INDEX IF NOT EXISTS idx_chat_crystallize_entries_embedding ON chat_crystallize_entries USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

