-- ==============================================================================
-- V7.2 Migration: data_operation (extract/update/delete Bible, Relation, Timeline, Chunking)
-- Chạy sau schema_v7_1_migration.sql
-- ==============================================================================

-- Bảng audit log cho thao tác dữ liệu (trích xuất/cập nhật/xóa) từ Chat V Work.
-- Dùng để theo dõi và hiển thị trạng thái; completion message được ghi vào chat_history.
CREATE TABLE IF NOT EXISTS data_operation_log (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  story_id UUID NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
  user_id TEXT,
  operation_type TEXT NOT NULL CHECK (operation_type IN ('extract', 'update', 'delete')),
  target TEXT NOT NULL CHECK (target IN ('bible', 'relation', 'timeline', 'chunking')),
  chapter_number INT,
  user_request TEXT,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'running', 'completed', 'failed')),
  error_message TEXT,
  completed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_data_operation_log_story ON data_operation_log(story_id);
CREATE INDEX IF NOT EXISTS idx_data_operation_log_created ON data_operation_log(created_at DESC);
COMMENT ON TABLE data_operation_log IS 'V7.2: Audit log thao tác dữ liệu (Bible/Relation/Timeline/Chunking) từ Chat';
