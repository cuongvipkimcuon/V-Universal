-- ==============================================================================
-- V9.2 Migration: Rule Type cho project_rules
-- - Thêm cột type (Style / Method / Info / Unknown) cho project_rules.
-- - Mặc định mọi rule hiện tại = 'Unknown'.
-- Chạy sau schema_v9_1_migration.sql.
-- ==============================================================================

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'project_rules' AND column_name = 'type'
  ) THEN
    ALTER TABLE project_rules ADD COLUMN type TEXT NOT NULL DEFAULT 'Unknown';
  END IF;
END $$;

COMMENT ON COLUMN project_rules.type IS
'V9.2: Phân loại rule: Style | Method | Info | Unknown.';

