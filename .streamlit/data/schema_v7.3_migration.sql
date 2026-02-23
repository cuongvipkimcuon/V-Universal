-- ==============================================================================
-- V7.3 Migration: Bộ chỉ lệnh @ — định nghĩa lệnh (cứng) + alias tùy biến (kích hoạt)
-- Chạy sau schema_v7.2_migration.sql
-- ==============================================================================

-- Bảng định nghĩa lệnh (chức năng cứng). Seed ~25-30 lệnh tương ứng thao tác hiện tại.
CREATE TABLE IF NOT EXISTS command_definitions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  command_key TEXT NOT NULL UNIQUE,
  name_vi TEXT NOT NULL,
  description TEXT NOT NULL,
  args_schema JSONB NOT NULL DEFAULT '[]',
  example_usage TEXT NOT NULL,
  default_trigger TEXT NOT NULL,
  intent TEXT,
  execution_note TEXT,
  sort_order INT NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE command_definitions IS 'V7.3: Định nghĩa lệnh cứng (command_key, args, intent). args_schema: [{name, required, type, description}].';
COMMENT ON COLUMN command_definitions.default_trigger IS 'Trigger mặc định không có @@, vd: extract. User gõ @@extract hoặc alias.';
COMMENT ON COLUMN command_definitions.intent IS 'Intent tương ứng (update_data, read_full_content, ...) hoặc action đặc biệt.';

-- Alias tùy biến: mỗi dự án/user có thể gán trigger thay thế cho lệnh.
CREATE TABLE IF NOT EXISTS command_aliases (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  story_id UUID NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
  user_id TEXT,
  alias TEXT NOT NULL,
  command_key TEXT NOT NULL REFERENCES command_definitions(command_key) ON DELETE CASCADE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(story_id, alias)
);

CREATE INDEX IF NOT EXISTS idx_command_aliases_story ON command_aliases(story_id);
CREATE INDEX IF NOT EXISTS idx_command_aliases_lookup ON command_aliases(story_id, LOWER(alias));
COMMENT ON TABLE command_aliases IS 'V7.3: Alias tùy biến (kích hoạt) cho lệnh. alias không chứa @@, vd: ex -> extract_bible.';

-- ==============================================================================
-- Seed: ~25 lệnh tương ứng thao tác / intent hiện có
-- ==============================================================================
INSERT INTO command_definitions (command_key, name_vi, description, args_schema, example_usage, default_trigger, intent, execution_note, sort_order) VALUES
('extract_bible', 'Trích xuất Bible', 'Chạy pipeline trích xuất Bible từ nội dung chương (nhân vật, địa điểm, khái niệm...)', '[{"name":"chapter_range","required":true,"type":"chapter_range","description":"Một chương (5) hoặc khoảng (1-10)"}]', '@@extract_bible 1-3 hoặc @@extract_bible 5', 'extract_bible', 'update_data', 'data_operation_type=extract, data_operation_target=bible', 1),
('extract_relation', 'Trích xuất Relation', 'Trích xuất quan hệ nhân vật từ chương', '[{"name":"chapter_range","required":true,"type":"chapter_range","description":"Một chương hoặc khoảng chương"}]', '@@extract_relation 2 hoặc @@extract_relation 1-5', 'extract_relation', 'update_data', 'data_operation_type=extract, data_operation_target=relation', 2),
('extract_timeline', 'Trích xuất Timeline', 'Trích xuất sự kiện timeline từ chương', '[{"name":"chapter_range","required":true,"type":"chapter_range","description":"Một chương hoặc khoảng"}]', '@@extract_timeline 3', 'extract_timeline', 'update_data', 'data_operation_type=extract, data_operation_target=timeline', 3),
('extract_chunking', 'Trích xuất Chunking', 'Chunk hóa nội dung chương', '[{"name":"chapter_range","required":true,"type":"chapter_range","description":"Một chương hoặc khoảng"}]', '@@extract_chunking 1-4', 'extract_chunking', 'update_data', 'data_operation_type=extract, data_operation_target=chunking', 4),
('delete_bible', 'Xóa Bible theo chương', 'Xóa dữ liệu Bible đã trích cho chương', '[{"name":"chapter_range","required":true,"type":"chapter_range","description":"Chương hoặc khoảng"}]', '@@delete_bible 2', 'delete_bible', 'update_data', 'data_operation_type=delete, data_operation_target=bible', 5),
('delete_relation', 'Xóa Relation theo chương', 'Xóa quan hệ đã trích theo chương', '[{"name":"chapter_range","required":true,"type":"chapter_range","description":"Chương hoặc khoảng"}]', '@@delete_relation 1-3', 'delete_relation', 'update_data', 'data_operation_type=delete, data_operation_target=relation', 6),
('delete_timeline', 'Xóa Timeline theo chương', 'Xóa sự kiện timeline theo chương', '[{"name":"chapter_range","required":true,"type":"chapter_range","description":"Chương hoặc khoảng"}]', '@@delete_timeline 2', 'delete_timeline', 'update_data', 'data_operation_type=delete, data_operation_target=timeline', 7),
('delete_chunking', 'Xóa Chunking theo chương', 'Xóa chunk đã tạo theo chương', '[{"name":"chapter_range","required":true,"type":"chapter_range","description":"Chương hoặc khoảng"}]', '@@delete_chunking 5', 'delete_chunking', 'update_data', 'data_operation_type=delete, data_operation_target=chunking', 8),
('data_analyze', 'Data Analyze (full 4 bước)', 'Chạy đủ 4 bước: trích Bible, Relation, Timeline, Chunking cho khoảng chương', '[{"name":"chapter_range","required":true,"type":"chapter_range","description":"Chương đơn hoặc khoảng (vd: 1-10)"}]', '@@data_analyze 1-5', 'data_analyze', 'update_data', 'Thực hiện 4 thao tác extract (bible, relation, timeline, chunking) cho chapter_range', 9),
('summarize_chapter', 'Tóm tắt chương', 'Đọc nội dung chương và tóm tắt', '[{"name":"chapter_range","required":true,"type":"chapter_range","description":"Chương cần tóm tắt (số hoặc khoảng)"}]', '@@summarize 1 hoặc @@summarize 2-4', 'summarize', 'read_full_content', 'rewritten_query = Tóm tắt chương X', 10),
('read_chapter', 'Đọc nội dung chương', 'Lấy toàn văn hoặc phần nội dung chương', '[{"name":"chapter_range","required":true,"type":"chapter_range","description":"Chương cần đọc"}]', '@@read 3', 'read', 'read_full_content', 'rewritten_query = Đọc/trích nội dung chương', 11),
('search_bible', 'Tìm trong Bible', 'Tìm kiếm lore, nhân vật, khái niệm trong Bible (kể cả crystallize)', '[{"name":"query","required":true,"type":"string","description":"Câu hỏi hoặc từ khóa"}]', '@@search_bible nhân vật A là ai', 'search_bible', 'search_bible', 'rewritten_query từ phần còn lại sau trigger', 12),
('search_chunks', 'Tìm trong Chunks', 'Tìm trong chunk đã chunking theo nội dung', '[{"name":"query","required":true,"type":"string","description":"Câu hỏi cần tìm"}]', '@@search_chunks Hùng sử dụng vũ khí gì', 'search_chunks', 'search_chunks', 'rewritten_query từ phần còn lại', 13),
('manage_timeline', 'Hỏi / so sánh Timeline', 'Hỏi thứ tự sự kiện, so sánh timeline', '[{"name":"query","required":true,"type":"string","description":"Câu hỏi về timeline"}]', '@@timeline so sánh sự kiện A và B', 'timeline', 'manage_timeline', 'rewritten_query từ phần còn lại', 14),
('mixed_context', 'Vừa đọc chương vừa tra Bible', 'Kết hợp nội dung chương + tra Bible/quan hệ trong chương đó', '[{"name":"chapter_range","required":true,"type":"chapter_range","description":"Chương"},{"name":"query","required":true,"type":"string","description":"Câu hỏi trong chương"}]', '@@mixed 3 nhân vật A làm gì và quan hệ với B', 'mixed', 'mixed_context', 'chapter_range + rewritten_query', 15),
('numerical_calculation', 'Tính toán số liệu', 'Tính tổng, trung bình, thống kê từ dữ liệu', '[{"name":"query","required":true,"type":"string","description":"Yêu cầu tính toán"}]', '@@calc tổng doanh thu 3 tháng', 'calc', 'numerical_calculation', 'rewritten_query từ phần còn lại', 16),
('web_search', 'Tìm kiếm web', 'Tra tỷ giá, tin tức, thời tiết, thông tin thực tế', '[{"name":"query","required":true,"type":"string","description":"Câu hỏi cần tra"}]', '@@web tỷ giá USD hôm nay', 'web', 'web_search', 'rewritten_query từ phần còn lại', 17),
('remember_rule', 'Ghi nhớ quy tắc', 'Ghi nhớ quy tắc/ghi chú vào hệ thống (rule)', '[{"name":"summary","required":true,"type":"string","description":"Nội dung cần ghi nhớ"}]', '@@remember cấm viết tắt tên nhân vật', 'remember', 'update_data', 'data_operation_type=remember_rule, data_operation_target=rule, update_summary', 18),
('query_sql', 'Truy vấn cấu trúc DB', 'Hỏi thuộc tính cấu trúc trong DB (trường, đối tượng)', '[{"name":"query","required":true,"type":"string","description":"Câu hỏi về cấu trúc dữ liệu"}]', '@@sql nhân vật A có trường parent_id không', 'sql', 'query_Sql', 'rewritten_query', 19),
('list_chapters', 'Liệt kê chương', 'Lấy danh sách chương (tên, số) của dự án', '[]', '@@chapters', 'chapters', 'read_full_content', 'Không cần chapter_range; rewritten_query = Liệt kê danh sách chương', 20),
('suggest_v7', 'Gợi ý dùng V7 (nhiều bước)', 'Gợi ý user bật V7 Planner khi câu hỏi cần nhiều bước', '[]', '@@v7 hoặc dùng khi câu phức tạp', 'v7', 'suggest_v7', 'Chỉ gợi ý; không thực thi nhiều bước', 21),
('ask_user_clarification', 'Hỏi lại người dùng', 'Khi thiếu thông tin: hệ thống sẽ hỏi lại thay vì đoán', '[]', '(Dùng nội bộ khi chỉ lệnh thiếu/sai)', 'clarify', 'ask_user_clarification', 'Fallback: không dùng làm lệnh trực tiếp', 22)
ON CONFLICT (command_key) DO UPDATE SET
  name_vi = EXCLUDED.name_vi,
  description = EXCLUDED.description,
  args_schema = EXCLUDED.args_schema,
  example_usage = EXCLUDED.example_usage,
  default_trigger = EXCLUDED.default_trigger,
  intent = EXCLUDED.intent,
  execution_note = EXCLUDED.execution_note,
  sort_order = EXCLUDED.sort_order;
