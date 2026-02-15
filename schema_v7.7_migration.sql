-- ==============================================================================
-- V7.7 Migration: Chapter logic check, Crystallize archive
-- - chapter_logic_checks: mỗi lần soát 1 chương
-- - chapter_logic_issues: từng lỗi logic (5 dimensions), dùng cờ resolved khi chạy lại không còn
-- - story_bible.archived: [CHAT] crystallize archive (không đưa vào context, không sửa)
-- Chạy sau schema_v7.6_migration.sql.
-- ==============================================================================

-- ------------------------------------------------------------------------------
-- 1) story_bible: cột archived (Crystallize [CHAT] archive)
-- ------------------------------------------------------------------------------
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'story_bible' AND column_name = 'archived') THEN
    ALTER TABLE story_bible ADD COLUMN archived BOOLEAN NOT NULL DEFAULT FALSE;
  END IF;
END $$;
CREATE INDEX IF NOT EXISTS idx_story_bible_archived ON story_bible(story_id, archived);
COMMENT ON COLUMN story_bible.archived IS 'V7.7: True = không đưa vào context, không cho sửa; chỉ nút Unarchive. Dùng cho [CHAT] crystallize.';

-- ------------------------------------------------------------------------------
-- 2) chapter_logic_checks: mỗi lần chạy soát 1 chương
-- ------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS chapter_logic_checks (
  id BIGSERIAL PRIMARY KEY,
  story_id UUID NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
  chapter_id BIGINT NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
  arc_id UUID REFERENCES arcs(id) ON DELETE SET NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'running', 'completed', 'failed')),
  checked_at TIMESTAMPTZ DEFAULT NOW(),
  result_summary TEXT,
  raw_llm_response TEXT,
  error_message TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_chapter_logic_checks_story ON chapter_logic_checks(story_id);
CREATE INDEX IF NOT EXISTS idx_chapter_logic_checks_chapter ON chapter_logic_checks(chapter_id);
CREATE INDEX IF NOT EXISTS idx_chapter_logic_checks_checked_at ON chapter_logic_checks(checked_at DESC);
COMMENT ON TABLE chapter_logic_checks IS 'V7.7: Mỗi lần soát logic 1 chương (5 dimensions: timeline, bible, relation, chat_crystallize, rule).';

-- ------------------------------------------------------------------------------
-- 3) chapter_logic_issues: từng lỗi; resolved = đã khắc phục (chạy lại không còn)
-- ------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS chapter_logic_issues (
  id BIGSERIAL PRIMARY KEY,
  story_id UUID NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
  chapter_id BIGINT NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
  check_id BIGINT REFERENCES chapter_logic_checks(id) ON DELETE SET NULL,
  dimension TEXT NOT NULL CHECK (dimension IN ('timeline', 'bible', 'relation', 'chat_crystallize', 'rule')),
  message TEXT NOT NULL,
  details JSONB DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'resolved')),
  created_at TIMESTAMPTZ DEFAULT NOW(),
  resolved_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_chapter_logic_issues_story ON chapter_logic_issues(story_id);
CREATE INDEX IF NOT EXISTS idx_chapter_logic_issues_chapter ON chapter_logic_issues(chapter_id);
CREATE INDEX IF NOT EXISTS idx_chapter_logic_issues_status ON chapter_logic_issues(story_id, status);
CREATE INDEX IF NOT EXISTS idx_chapter_logic_issues_check ON chapter_logic_issues(check_id);
COMMENT ON TABLE chapter_logic_issues IS 'V7.7: Từng lỗi logic theo chương. status=resolved khi chạy lại soát mà lỗi không còn.';
