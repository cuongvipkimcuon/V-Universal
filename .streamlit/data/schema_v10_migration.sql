-- ==============================================================================
-- V10 Migration: Bản thiết kế Đại Chỉnh Sửa — Root/Branch cho story_bible
-- Chạy trong Supabase SQL Editor. Chạy sau các migration V9.x.
-- ==============================================================================
-- Triết lý: Bible Entity = Mỏ neo ID. Root = tóm tắt lũy tiến; Branch = trạng thái theo chương.
-- ==============================================================================

-- 1) story_bible: thêm node_type (root | branch)
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'story_bible' AND column_name = 'node_type') THEN
    ALTER TABLE story_bible ADD COLUMN node_type TEXT DEFAULT 'root'
      CHECK (node_type IN ('root', 'branch'));
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_story_bible_node_type ON story_bible(story_id, node_type);
COMMENT ON COLUMN story_bible.node_type IS 'V10: root = tóm tắt lũy tiến (tổng hợp đã biết), branch = trạng thái mới/đổi ở chương cụ thể (source_chapter).';

-- 2) Đảm bảo parent_id đã có (từ V5) — không thêm nếu đã có
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'story_bible' AND column_name = 'parent_id') THEN
    ALTER TABLE story_bible ADD COLUMN parent_id UUID REFERENCES story_bible(id) ON DELETE SET NULL;
  END IF;
END $$;

-- 3) Cập nhật các bản ghi hiện tại: mặc định node_type = 'root' (entity đã có coi như Root)
UPDATE story_bible SET node_type = 'root' WHERE node_type IS NULL OR node_type = '';
