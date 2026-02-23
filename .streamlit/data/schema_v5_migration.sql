-- ==============================================================================
-- V5 Migration: Đồng bộ Schema, cột thiếu, Index. Defensive: IF NOT EXISTS / DO.
-- Chạy trong Supabase SQL Editor. Chạy schema_prefix_persona.sql trước nếu chưa có bible_prefix_config.
-- ==============================================================================

-- 1) Chapters: thêm summary, art_style (nếu chưa có)
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'chapters' AND column_name = 'summary') THEN
    ALTER TABLE chapters ADD COLUMN summary TEXT DEFAULT '';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'chapters' AND column_name = 'art_style') THEN
    ALTER TABLE chapters ADD COLUMN art_style TEXT DEFAULT '';
  END IF;
END $$;

-- 2) Story Bible: thêm parent_id, lookup_count, importance_bias, last_lookup_at (nếu chưa có)
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'story_bible' AND column_name = 'parent_id') THEN
    ALTER TABLE story_bible ADD COLUMN parent_id UUID REFERENCES story_bible(id) ON DELETE SET NULL;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'story_bible' AND column_name = 'lookup_count') THEN
    ALTER TABLE story_bible ADD COLUMN lookup_count INT NOT NULL DEFAULT 0;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'story_bible' AND column_name = 'importance_bias') THEN
    ALTER TABLE story_bible ADD COLUMN importance_bias NUMERIC(5,2) DEFAULT 0.5;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'story_bible' AND column_name = 'last_lookup_at') THEN
    ALTER TABLE story_bible ADD COLUMN last_lookup_at TIMESTAMPTZ;
  END IF;
END $$;

-- 3) Entity Relations: chỉ thêm index (bảng có thể đã tồn tại với source_entity_id hoặc entity_id)

-- 4) Entity Setup (Prefix động): view trỏ tới bible_prefix_config
DROP VIEW IF EXISTS entity_setup;
CREATE OR REPLACE VIEW entity_setup AS
  SELECT id, prefix_key, description, sort_order, created_at
  FROM bible_prefix_config;

-- 5) Index: Xóa index cũ không dùng (tùy chọn, chỉ khi biết tên cụ thể)
-- CREATE INDEX ... không gây lỗi nếu đã tồn tại khi dùng IF NOT EXISTS

-- Index cho story_bible
CREATE INDEX IF NOT EXISTS idx_story_bible_story_id ON story_bible(story_id);
CREATE INDEX IF NOT EXISTS idx_story_bible_parent_id ON story_bible(parent_id);
CREATE INDEX IF NOT EXISTS idx_story_bible_lookup_importance ON story_bible(story_id, lookup_count, importance_bias);

-- Index cho chapters
CREATE INDEX IF NOT EXISTS idx_chapters_story_id ON chapters(story_id);
CREATE INDEX IF NOT EXISTS idx_chapters_story_chapter ON chapters(story_id, chapter_number);

-- Index cho entity_relations (bảng phải đã tồn tại)
CREATE INDEX IF NOT EXISTS idx_entity_relations_story_id ON entity_relations(story_id);
CREATE INDEX IF NOT EXISTS idx_entity_relations_target ON entity_relations(target_entity_id);
