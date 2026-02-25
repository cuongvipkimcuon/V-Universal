-- ==============================================================================
-- V9.3 Migration: project_rule_arcs - hỗ trợ 1 Rule gắn nhiều Arc
-- - Tạo bảng project_rule_arcs (rule_id <-> arc_id) để map N-N.
-- - Backfill từ các project_rules scope='arc' hiện có (dùng cột arc_id cũ).
--   Chạy sau schema_v9_2_migration.sql.
-- ==============================================================================

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_name = 'project_rule_arcs'
  ) THEN
    CREATE TABLE project_rule_arcs (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      rule_id uuid NOT NULL REFERENCES project_rules(id) ON DELETE CASCADE,
      arc_id uuid NOT NULL REFERENCES arcs(id) ON DELETE CASCADE,
      created_at timestamptz DEFAULT now()
    );

    CREATE INDEX IF NOT EXISTS idx_project_rule_arcs_rule_id ON project_rule_arcs(rule_id);
    CREATE INDEX IF NOT EXISTS idx_project_rule_arcs_arc_id ON project_rule_arcs(arc_id);
  END IF;
END $$;

-- Backfill: mọi rule scope='arc' có arc_id hiện tại -> insert mapping tương ứng.
INSERT INTO project_rule_arcs (rule_id, arc_id)
SELECT id, arc_id
FROM project_rules
WHERE scope = 'arc' AND arc_id IS NOT NULL;

