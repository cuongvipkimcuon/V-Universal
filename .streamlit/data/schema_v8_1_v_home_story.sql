-- ==============================================================================
-- V8.1: V Home gắn với project (story_id)
-- Chạy sau schema_v8_migration.sql. Thêm story_id vào v_home để mỗi project có topic/tin riêng.
-- ==============================================================================

-- v_home_current_topic: thêm story_id, đổi PK thành (user_id, story_id)
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'v_home_current_topic' AND column_name = 'story_id') THEN
    ALTER TABLE v_home_current_topic ADD COLUMN story_id UUID NULL REFERENCES stories(id) ON DELETE CASCADE;
  END IF;
END $$;

-- Cho phép nhiều dòng per user (mỗi project một topic). Bỏ PK cũ.
ALTER TABLE v_home_current_topic DROP CONSTRAINT IF EXISTS v_home_current_topic_pkey;
-- Một topic per (user_id, story_id) khi story_id NOT NULL; một topic per user khi story_id NULL (legacy).
CREATE UNIQUE INDEX IF NOT EXISTS idx_v_home_current_topic_user_story
  ON v_home_current_topic (user_id, story_id) WHERE story_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_v_home_current_topic_user_legacy
  ON v_home_current_topic (user_id) WHERE story_id IS NULL;

-- v_home_messages: thêm story_id
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'v_home_messages' AND column_name = 'story_id') THEN
    ALTER TABLE v_home_messages ADD COLUMN story_id UUID NULL REFERENCES stories(id) ON DELETE CASCADE;
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_v_home_messages_story ON v_home_messages(story_id);
CREATE INDEX IF NOT EXISTS idx_v_home_messages_user_story_topic ON v_home_messages(user_id, story_id, topic_start_at);

COMMENT ON COLUMN v_home_current_topic.story_id IS 'V8.1: Project (null = legacy, một topic chung).';
COMMENT ON COLUMN v_home_messages.story_id IS 'V8.1: Project (null = legacy).';
