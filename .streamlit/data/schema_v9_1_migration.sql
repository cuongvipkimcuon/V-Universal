-- ==============================================================================
-- V9.1 Migration: Cờ approve cho Rules & Semantic Intent
-- - Thêm cột approve (boolean, mặc định TRUE) cho project_rules và semantic_intent.
-- - Luật / Semantic Intent mới trích xuất tự động sẽ đặt approve = FALSE, chỉ dùng sau khi user duyệt.
-- Chạy sau schema_v9_0_migration.sql.
-- ==============================================================================

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'project_rules' AND column_name = 'approve'
  ) THEN
    ALTER TABLE project_rules ADD COLUMN approve BOOLEAN NOT NULL DEFAULT TRUE;
  END IF;
END $$;

COMMENT ON COLUMN project_rules.approve IS 'V9.1: TRUE = rule đã được user duyệt; FALSE = mới trích xuất, chưa duyệt.';

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'semantic_intent' AND column_name = 'approve'
  ) THEN
    ALTER TABLE semantic_intent ADD COLUMN approve BOOLEAN NOT NULL DEFAULT TRUE;
  END IF;
END $$;

COMMENT ON COLUMN semantic_intent.approve IS 'V9.1: TRUE = mẫu semantic intent đã được user duyệt; FALSE = mới sinh tự động, chưa duyệt.';

