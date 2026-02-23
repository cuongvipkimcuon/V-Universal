-- Giai đoạn 4: Bảng cho Collaboration (chạy trong Supabase SQL Editor nếu chưa có).

-- Bảng thành viên project (share project theo email)
CREATE TABLE IF NOT EXISTS project_members (
  id BIGSERIAL PRIMARY KEY,
  story_id UUID NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
  user_email TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'viewer' CHECK (role IN ('owner', 'partner', 'viewer')),
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(story_id, user_email)
);

-- Bảng yêu cầu thay đổi (Partner gửi, Owner duyệt/từ chối)
CREATE TABLE IF NOT EXISTS pending_changes (
  id BIGSERIAL PRIMARY KEY,
  story_id UUID NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
  requested_by_email TEXT NOT NULL,
  table_name TEXT NOT NULL,
  target_key JSONB DEFAULT '{}',
  old_data JSONB DEFAULT '{}',
  new_data JSONB DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Gợi ý index
CREATE INDEX IF NOT EXISTS idx_project_members_story ON project_members(story_id);
CREATE INDEX IF NOT EXISTS idx_project_members_email ON project_members(user_email);
CREATE INDEX IF NOT EXISTS idx_pending_changes_story_status ON pending_changes(story_id, status);
