-- ==============================================================================
-- V7.5 Migration: Bảng tác vụ chạy ngầm (Data Analyze + Chat data operation)
-- Dùng để hiển thị tab "Tác vụ ngầm" trong Workstation và đảm bảo thông báo V Work khi xong.
-- Chạy sau schema_v7.4_migration.sql
-- ==============================================================================

CREATE TABLE IF NOT EXISTS background_jobs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  story_id UUID NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
  user_id TEXT,
  job_type TEXT NOT NULL,
  label TEXT NOT NULL,
  payload JSONB DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'running', 'completed', 'failed')),
  result_summary TEXT,
  error_message TEXT,
  post_to_chat BOOLEAN NOT NULL DEFAULT true,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_background_jobs_story ON background_jobs(story_id);
CREATE INDEX IF NOT EXISTS idx_background_jobs_status ON background_jobs(status);
CREATE INDEX IF NOT EXISTS idx_background_jobs_created ON background_jobs(created_at DESC);

COMMENT ON TABLE background_jobs IS 'V7.5: Tác vụ chạy ngầm (Data Analyze, data_operation_batch từ Chat). Tab Tác vụ ngầm hiển thị danh sách và kết quả.';
