-- ==============================================================================
-- V8.3 Migration: Observability (chat_turn_logs), Feature flags (settings keys)
-- Chạy sau schema_v8_2_migration.sql.
-- Dùng cho: log intent, context_needs, token_count, llm_calls mỗi turn; feature flags V8.
-- ==============================================================================

-- ------------------------------------------------------------------------------
-- 1) chat_turn_logs — log mỗi lượt chat (intent, context, token, số lần gọi LLM)
-- ------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS chat_turn_logs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  story_id UUID NULL REFERENCES stories(id) ON DELETE SET NULL,
  user_id TEXT NULL,
  intent TEXT NULL,
  context_needs JSONB NULL,
  context_tokens INT NULL,
  llm_calls_count INT NULL DEFAULT 0,
  verification_used BOOLEAN NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chat_turn_logs_story ON chat_turn_logs(story_id);
CREATE INDEX IF NOT EXISTS idx_chat_turn_logs_user ON chat_turn_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_chat_turn_logs_created ON chat_turn_logs(created_at DESC);

COMMENT ON TABLE chat_turn_logs IS 'V8.3: Observability — log intent, context_needs, token_count, llm_calls mỗi turn chat.';

-- ------------------------------------------------------------------------------
-- 2) Settings keys cho V8 (insert nếu chưa có; dùng upsert ở app)
-- v8_full_context_search: 1 = search_context luôn gather đủ bible/chunk/relation/timeline/chapter (đã hardcode trong code, key để tắt nếu cần)
-- max_llm_calls_per_turn: số tối đa gọi LLM mỗi turn (0 = không giới hạn)
-- ------------------------------------------------------------------------------
-- App sẽ đọc/ghi qua table settings (key, value). Không cần insert mặc định ở đây.
