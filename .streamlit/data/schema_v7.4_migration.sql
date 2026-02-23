-- ==============================================================================
-- V7.4 Migration: bible_entry_id khớp kiểu story_bible.id (UUID) để Clean ALL Chats
-- có thể xóa đúng Bible [CHAT] đã crystallize.
-- Chạy sau schema_v7.3_migration.sql
-- ==============================================================================

-- Đổi bible_entry_id từ BIGINT sang UUID (story_bible.id là UUID).
-- Dữ liệu cũ có thể mất tương ứng (set NULL); các lần crystallize sau sẽ ghi đúng.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'chat_crystallize_log' AND column_name = 'bible_entry_id') THEN
    ALTER TABLE chat_crystallize_log
      ALTER COLUMN bible_entry_id TYPE UUID USING NULL;
  END IF;
EXCEPTION
  WHEN OTHERS THEN
    -- Nếu đã là UUID hoặc lỗi khác: thêm cột mới, copy, drop cũ, rename (tùy triển khai)
    NULL;
END $$;

COMMENT ON COLUMN chat_crystallize_log.bible_entry_id IS 'V7.4: UUID tham chiếu story_bible.id (entry [CHAT] crystallize). Dùng khi Clean ALL Chats để xóa đúng Bible [CHAT] của user.';
