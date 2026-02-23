-- ==============================================================================
-- V8 Migration: Chuẩn hóa mô hình 1-N cho Chunk, Bible, Timeline, Relation
-- Quan hệ parent-child đầy đủ; bổ sung Arc và bảng link.
-- Chạy sau schema_v7.7_migration.sql.
-- ==============================================================================

-- ------------------------------------------------------------------------------
-- 1) BẢNG LINK: chunk_bible_links (Chunk 1-N Bible)
-- Một chunk có thể tham chiếu nhiều bible entry; một bible entry có thể xuất hiện trong nhiều chunk.
-- Parent: chunk (1) — Child: link rows (N). Cũng xem bible entry là parent của link (bible 1-N links).
-- ------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS chunk_bible_links (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  story_id UUID NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
  chunk_id UUID NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
  bible_entry_id BIGINT NOT NULL REFERENCES story_bible(id) ON DELETE CASCADE,
  mention_role TEXT DEFAULT NULL CHECK (mention_role IS NULL OR mention_role IN ('primary', 'secondary', 'mention', 'other')),
  sort_order INT NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(chunk_id, bible_entry_id)
);

CREATE INDEX IF NOT EXISTS idx_chunk_bible_links_story ON chunk_bible_links(story_id);
CREATE INDEX IF NOT EXISTS idx_chunk_bible_links_chunk ON chunk_bible_links(chunk_id);
CREATE INDEX IF NOT EXISTS idx_chunk_bible_links_bible ON chunk_bible_links(bible_entry_id);

COMMENT ON TABLE chunk_bible_links IS 'V8: Liên kết 1-N giữa chunk và story_bible. Dùng cho unified extract và chuẩn hóa dữ liệu cũ.';
COMMENT ON COLUMN chunk_bible_links.mention_role IS 'Vai trò xuất hiện: primary/secondary/mention/other (tùy pipeline).';

-- ------------------------------------------------------------------------------
-- 2) BẢNG LINK: chunk_timeline_links (Chunk 1-N Timeline)
-- Một chunk có thể liên quan nhiều sự kiện timeline; một sự kiện có thể được nhắc trong nhiều chunk.
-- Parent: chunk (1) — Child: link rows (N). Timeline event là parent của link (1-N links).
-- ------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS chunk_timeline_links (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  story_id UUID NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
  chunk_id UUID NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
  timeline_event_id UUID NOT NULL REFERENCES timeline_events(id) ON DELETE CASCADE,
  mention_role TEXT DEFAULT NULL CHECK (mention_role IS NULL OR mention_role IN ('primary', 'secondary', 'mention', 'other')),
  sort_order INT NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(chunk_id, timeline_event_id)
);

CREATE INDEX IF NOT EXISTS idx_chunk_timeline_links_story ON chunk_timeline_links(story_id);
CREATE INDEX IF NOT EXISTS idx_chunk_timeline_links_chunk ON chunk_timeline_links(chunk_id);
CREATE INDEX IF NOT EXISTS idx_chunk_timeline_links_timeline ON chunk_timeline_links(timeline_event_id);

COMMENT ON TABLE chunk_timeline_links IS 'V8: Liên kết 1-N giữa chunk và timeline_events. Dùng cho unified extract và chuẩn hóa.';

-- ------------------------------------------------------------------------------
-- 3) entity_relations: thêm nguồn (parent chapter/chunk) để quan hệ có traceability
-- Relation là "child" của chapter hoặc chunk (trích xuất từ đâu). 1 chapter → N relations; 1 chunk → N relations.
-- ------------------------------------------------------------------------------
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'entity_relations' AND column_name = 'source_chapter') THEN
    ALTER TABLE entity_relations ADD COLUMN source_chapter INT NULL;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'entity_relations' AND column_name = 'source_chunk_id') THEN
    ALTER TABLE entity_relations ADD COLUMN source_chunk_id UUID NULL REFERENCES chunks(id) ON DELETE SET NULL;
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_entity_relations_source_chapter ON entity_relations(story_id, source_chapter) WHERE source_chapter IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_entity_relations_source_chunk ON entity_relations(source_chunk_id) WHERE source_chunk_id IS NOT NULL;

COMMENT ON COLUMN entity_relations.source_chapter IS 'V8: Chương nguồn trích xuất quan hệ (parent chapter).';
COMMENT ON COLUMN entity_relations.source_chunk_id IS 'V8: Chunk nguồn trích xuất quan hệ (parent chunk).';

-- ------------------------------------------------------------------------------
-- 4) story_bible: thêm source_chunk_id (tùy chọn) — bible entry lần đầu xuất hiện ở chunk nào
-- Đã có source_chapter (parent chapter). Thêm granularity chunk nếu cần.
-- ------------------------------------------------------------------------------
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'story_bible' AND column_name = 'source_chunk_id') THEN
    ALTER TABLE story_bible ADD COLUMN source_chunk_id UUID NULL REFERENCES chunks(id) ON DELETE SET NULL;
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_story_bible_source_chunk ON story_bible(source_chunk_id) WHERE source_chunk_id IS NOT NULL;

COMMENT ON COLUMN story_bible.source_chunk_id IS 'V8: Chunk nguồn (lần đầu xuất hiện). Nullable; source_chapter vẫn là nguồn chính.';

-- ------------------------------------------------------------------------------
-- 5) timeline_events: thêm source_chunk_id (tùy chọn) — sự kiện trích từ chunk nào
-- Đã có chapter_id (parent chapter). Thêm chunk nếu pipeline trích event theo từng chunk.
-- ------------------------------------------------------------------------------
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'timeline_events' AND column_name = 'source_chunk_id') THEN
    ALTER TABLE timeline_events ADD COLUMN source_chunk_id UUID NULL REFERENCES chunks(id) ON DELETE SET NULL;
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_timeline_events_source_chunk ON timeline_events(source_chunk_id) WHERE source_chunk_id IS NOT NULL;

COMMENT ON COLUMN timeline_events.source_chunk_id IS 'V8: Chunk nguồn trích xuất sự kiện. Nullable; chapter_id vẫn là nguồn chính.';

-- ------------------------------------------------------------------------------
-- 6) Arc: bổ sung cột cho UI và mở rộng (không bắt buộc)
-- Arc đã có parent_arc_id, prev_arc_id (parent-child). Thêm metadata hiển thị.
-- ------------------------------------------------------------------------------
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'arcs' AND column_name = 'description') THEN
    ALTER TABLE arcs ADD COLUMN description TEXT DEFAULT '';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'arcs' AND column_name = 'color_hex') THEN
    ALTER TABLE arcs ADD COLUMN color_hex TEXT DEFAULT NULL;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'arcs' AND column_name = 'display_order') THEN
    ALTER TABLE arcs ADD COLUMN display_order INT NOT NULL DEFAULT 0;
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_arcs_display_order ON arcs(story_id, display_order);

COMMENT ON COLUMN arcs.description IS 'V8: Mô tả dài cho arc (khác summary ngắn).';
COMMENT ON COLUMN arcs.color_hex IS 'V8: Màu hiển thị (hex, VD: #4A90D9).';
COMMENT ON COLUMN arcs.display_order IS 'V8: Thứ tự hiển thị (UI); sort_order vẫn dùng cho logic.';

-- ------------------------------------------------------------------------------
-- 7) Bảng theo dõi lần chạy unified extract (tùy chọn — phân biệt data unified vs legacy)
-- Một chapter có thể có nhiều lần chạy; mỗi lần tạo 1 run. Link tables có thể gắn run_id sau.
-- ------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS unified_extract_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  story_id UUID NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
  chapter_id BIGINT NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
  run_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  status TEXT NOT NULL DEFAULT 'completed' CHECK (status IN ('pending', 'running', 'completed', 'failed', 'partial')),
  bible_count INT NOT NULL DEFAULT 0,
  timeline_count INT NOT NULL DEFAULT 0,
  chunk_count INT NOT NULL DEFAULT 0,
  relation_count INT NOT NULL DEFAULT 0,
  link_bible_count INT NOT NULL DEFAULT 0,
  link_timeline_count INT NOT NULL DEFAULT 0,
  error_message TEXT,
  meta_json JSONB DEFAULT '{}',
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_unified_extract_runs_story ON unified_extract_runs(story_id);
CREATE INDEX IF NOT EXISTS idx_unified_extract_runs_chapter ON unified_extract_runs(chapter_id);
CREATE INDEX IF NOT EXISTS idx_unified_extract_runs_run_at ON unified_extract_runs(run_at DESC);

COMMENT ON TABLE unified_extract_runs IS 'V8: Mỗi lần chạy pipeline unified extract cho một chương. Dùng để biết data từ run nào (unified vs chuẩn hóa/legacy).';

-- ------------------------------------------------------------------------------
-- 8) Chuẩn hóa: bảng ghi lần chạy chuẩn hóa link (cho data cũ)
-- Khác unified extract: chuẩn hóa chỉ gán link từ dữ liệu đã có (chunk, bible, timeline).
-- ------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS normalize_links_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  story_id UUID NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
  scope_type TEXT NOT NULL CHECK (scope_type IN ('chapter', 'project')),
  chapter_id BIGINT NULL REFERENCES chapters(id) ON DELETE SET NULL,
  run_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  status TEXT NOT NULL DEFAULT 'completed' CHECK (status IN ('pending', 'running', 'completed', 'failed', 'partial')),
  chunks_processed INT NOT NULL DEFAULT 0,
  link_bible_added INT NOT NULL DEFAULT 0,
  link_timeline_added INT NOT NULL DEFAULT 0,
  error_message TEXT,
  meta_json JSONB DEFAULT '{}',
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_normalize_links_runs_story ON normalize_links_runs(story_id);
CREATE INDEX IF NOT EXISTS idx_normalize_links_runs_run_at ON normalize_links_runs(run_at DESC);

COMMENT ON TABLE normalize_links_runs IS 'V8: Mỗi lần chạy chuẩn hóa link (chunk_bible_links, chunk_timeline_links) trên data hiện có.';
