-- ==============================================================================
-- V8.9 Migration: Bảng riêng cho Rules và Chat Crystallize (phân cấp global, project, arc)
-- Bible không còn lưu [RULE] và [CHAT]. Scope: global (qua nhiều project), project, arc.
-- Chạy sau schema_v8_8_migration.sql
-- ==============================================================================

-- 1) Bảng rules: luật áp dụng theo phạm vi (global / project / arc)
-- ------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS project_rules (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  scope TEXT NOT NULL DEFAULT 'project' CHECK (scope IN ('global', 'project', 'arc')),
  story_id UUID REFERENCES stories(id) ON DELETE CASCADE,
  arc_id UUID REFERENCES arcs(id) ON DELETE CASCADE,
  content TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_project_rules_scope ON project_rules(scope);
CREATE INDEX IF NOT EXISTS idx_project_rules_story ON project_rules(story_id) WHERE story_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_project_rules_arc ON project_rules(arc_id) WHERE arc_id IS NOT NULL;

COMMENT ON TABLE project_rules IS 'V8.9: Luật theo phạm vi. global: story_id NULL; project: story_id NOT NULL, arc_id NULL; arc: story_id + arc_id.';

-- 2) Bảng chat_crystallize_entries: bộ nhớ crystallize (không còn lưu vào story_bible)
-- ------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS chat_crystallize_entries (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  scope TEXT NOT NULL DEFAULT 'project' CHECK (scope IN ('global', 'project', 'arc')),
  story_id UUID REFERENCES stories(id) ON DELETE CASCADE,
  arc_id UUID REFERENCES arcs(id) ON DELETE CASCADE,
  user_id TEXT,
  title TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '',
  message_count INT NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chat_crystallize_entries_scope ON chat_crystallize_entries(scope);
CREATE INDEX IF NOT EXISTS idx_chat_crystallize_entries_story ON chat_crystallize_entries(story_id) WHERE story_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_chat_crystallize_entries_created ON chat_crystallize_entries(created_at DESC);

COMMENT ON TABLE chat_crystallize_entries IS 'V8.9: Tóm tắt chat crystallize. global: story_id NULL; project/arc tương tự rules.';

-- 3) chat_crystallize_log: thêm cột trỏ tới entry mới (bible_entry_id giữ cho tương thích cũ)
-- ------------------------------------------------------------------------------
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'chat_crystallize_log' AND column_name = 'crystallize_entry_id') THEN
    ALTER TABLE chat_crystallize_log ADD COLUMN crystallize_entry_id UUID REFERENCES chat_crystallize_entries(id) ON DELETE SET NULL;
  END IF;
END $$;

COMMENT ON COLUMN chat_crystallize_log.crystallize_entry_id IS 'V8.9: FK tới chat_crystallize_entries. bible_entry_id vẫn dùng cho dữ liệu cũ.';
