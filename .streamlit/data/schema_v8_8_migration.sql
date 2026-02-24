-- ==============================================================================
-- V8.8 Migration: Bảng lưu kết quả LLM cho job (unified, bible, relation, timeline, chunk)
-- Dùng để: 1) Lưu kết quả LLM sau mỗi lần gọi; 2) Retry khi thất bại không gọi LLM lại;
-- 3) Backup/audit sau này.
-- Chạy sau schema_v8_7_migration.sql
-- ==============================================================================

CREATE TABLE IF NOT EXISTS job_llm_results (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  job_id UUID NOT NULL,
  step_type TEXT NOT NULL,
  step_key TEXT NOT NULL DEFAULT '',
  input_snapshot JSONB DEFAULT '{}',
  llm_raw_response TEXT,
  parsed_result JSONB,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'success', 'failed')),
  error_message TEXT,
  retry_count INT NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_job_llm_results_job_id ON job_llm_results(job_id);
CREATE INDEX IF NOT EXISTS idx_job_llm_results_job_step ON job_llm_results(job_id, step_type, step_key);
CREATE INDEX IF NOT EXISTS idx_job_llm_results_updated ON job_llm_results(updated_at DESC);

COMMENT ON TABLE job_llm_results IS 'V8.8: Kết quả LLM theo job/step; retry dùng parsed_result, không gọi LLM lại.';
