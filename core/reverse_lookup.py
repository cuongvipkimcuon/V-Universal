# core/reverse_lookup.py - V6 MODULE 3: REVERSE LOOKUP & CONTEXT ASSEMBLER (The Brain)
"""
Triangle Logic: Trace context from Micro (chunk) -> Meso (chapter) -> Macro (arc).
Assemble prompt: [MACRO CONTEXT - ARC] / [MESO CONTEXT - CHAPTER] / [MICRO EVIDENCE - CHUNK].
"""
from typing import Dict, List, Optional, Any, Tuple

from config import init_services


class ReverseLookupAssembler:
    """Vertical reverse lookup: Chunk -> Chapter -> Arc. Build strict-order context."""

    @staticmethod
    def _supabase():
        s = init_services()
        return s["supabase"] if s else None

    @staticmethod
    def get_chunk_with_parents(chunk_id: str) -> Optional[Dict[str, Any]]:
        """
        Fetch chunk by id, then trace up to chapter and arc.
        Returns {chunk, chapter, arc} (chapter/arc may be None if not linked).
        """
        supabase = ReverseLookupAssembler._supabase()
        if not supabase or not chunk_id:
            return None
        try:
            r = supabase.table("chunks").select("*").eq("id", chunk_id).limit(1).execute()
            if not r.data:
                return None
            chunk = r.data[0]
            chapter = None
            arc = None
            chapter_id = chunk.get("chapter_id")
            arc_id = chunk.get("arc_id")
            if chapter_id:
                cr = supabase.table("chapters").select("*").eq("id", chapter_id).limit(1).execute()
                if cr.data:
                    chapter = cr.data[0]
                    if not arc_id and chapter.get("arc_id"):
                        arc_id = chapter["arc_id"]
            if arc_id:
                ar = supabase.table("arcs").select("*").eq("id", arc_id).limit(1).execute()
                if ar.data:
                    arc = ar.data[0]
            return {"chunk": chunk, "chapter": chapter, "arc": arc}
        except Exception:
            return None

    @staticmethod
    def assemble_single(chunk_id: str) -> str:
        """
        Build one block of context for a chunk in strict order:
        [MACRO CONTEXT - ARC] -> [MESO CONTEXT - CHAPTER] -> [MICRO EVIDENCE - CHUNK].
        V8.x: MICRO EVIDENCE dùng "window" các chunk lân cận trong cùng chương
        (vd. chunk trước và sau) để LLM có đủ ngữ cảnh đoạn, không phải 1 chunk lẻ.
        """
        data = ReverseLookupAssembler.get_chunk_with_parents(chunk_id)
        if not data:
            return ""
        chunk = data["chunk"]
        chapter = data["chapter"]
        arc = data["arc"]
        parts = []
        if arc:
            parts.append(
                "[MACRO CONTEXT - ARC: %s]\nSummary: %s"
                % (arc.get("name") or "Unnamed", (arc.get("summary") or "").strip() or "(none)")
            )
        if chapter:
            parts.append(
                "[MESO CONTEXT - CHAPTER: %s]\nSummary: %s"
                % (
                    (chapter.get("title") or "Unnamed").strip(),
                    (chapter.get("summary") or "").strip() or "(none)",
                )
            )
        # MICRO: thay vì chỉ 1 chunk lẻ, lấy window các chunk lân cận trong cùng chương (ví dụ chunk trước/sau).
        supabase = ReverseLookupAssembler._supabase()
        neighbor_window = 1  # ±1 chunk quanh chunk chính
        neighbor_chunks: List[Dict[str, Any]] = []
        try:
            if supabase and chapter and chunk.get("sort_order") is not None:
                story_id = chunk.get("story_id")
                chapter_id = chapter.get("id") or chunk.get("chapter_id")
                if story_id and chapter_id:
                    sort_val = int(chunk.get("sort_order") or 0)
                    r = (
                        supabase.table("chunks")
                        .select("id, content, raw_content, meta_json, sort_order")
                        .eq("story_id", story_id)
                        .eq("chapter_id", chapter_id)
                        .gte("sort_order", sort_val - neighbor_window)
                        .lte("sort_order", sort_val + neighbor_window)
                        .order("sort_order")
                        .execute()
                    )
                    neighbor_chunks = list(r.data or [])
        except Exception:
            neighbor_chunks = []
        if not neighbor_chunks:
            neighbor_chunks = [chunk]

        micro_blocks = []
        for nc in neighbor_chunks:
            meta = nc.get("meta_json") or {}
            if isinstance(meta, str):
                try:
                    import json
                    meta = json.loads(meta) if meta else {}
                except Exception:
                    meta = {}
            source_meta = meta.get("source_metadata", meta)
            if isinstance(source_meta, dict):
                source_str = ", ".join("%s=%s" % (k, v) for k, v in source_meta.items())
            else:
                source_str = str(source_meta)
            title = ""
            if isinstance(meta, dict):
                title = (meta.get("title") or "") if meta else ""
            if not title and isinstance(source_meta, dict):
                title = (source_meta.get("title") or "") if source_meta else ""
            sort_label = nc.get("sort_order")
            header = "[MICRO EVIDENCE - REVERSE SOURCE: %s]" % (source_str or "(none)")
            if sort_label is not None or title:
                header += " (chunk #%s%s)" % (
                    str(sort_label) if sort_label is not None else "?",
                    f" - {title}" if title else "",
                )
            content = (nc.get("content") or nc.get("raw_content") or "").strip()
            micro_blocks.append(f"{header}\nContent: {content}")

        parts.append("\n\n".join(micro_blocks))
        return "\n\n".join(parts)

    @staticmethod
    def assemble_from_chunks(chunk_ids: List[str], token_limit: int = 0) -> Tuple[str, List[str]]:
        """
        Build full context string from multiple chunks (triangle for each).
        Returns (assembled_text, list of source labels for UI).
        """
        from ai_engine import AIService
        total_tokens = 0
        blocks = []
        sources = []
        for cid in chunk_ids:
            block = ReverseLookupAssembler.assemble_single(cid)
            if not block:
                continue
            t = AIService.estimate_tokens(block)
            if token_limit > 0 and total_tokens + t > token_limit:
                continue
            total_tokens += t
            blocks.append(block)
            data = ReverseLookupAssembler.get_chunk_with_parents(cid)
            if data and data.get("chunk"):
                meta = (data["chunk"].get("meta_json") or {}) or {}
                sm = meta.get("source_metadata", meta) if isinstance(meta, dict) else {}
                label = sm.get("sheet_name", "") or sm.get("source_file", "") or cid[:8]
                sources.append("Chunk %s" % label)
        return "\n\n---\n\n".join(blocks), sources

    @staticmethod
    def search_chunks(
        project_id: str,
        arc_id: Optional[str],
        query: str,
        top_k: int = 10,
        scope_sequential: bool = False,
        past_arc_ids: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Search chunks by text (content or raw_content ilike). Optionally filter by arc.
        If scope_sequential, include chunks from past_arc_ids + current arc_id.
        Returns list of chunk rows (with id, chapter_id, arc_id, content, meta_json).
        """
        supabase = ReverseLookupAssembler._supabase()
        if not supabase or not project_id:
            return []
        try:
            q = supabase.table("chunks").select("id, chapter_id, arc_id, content, raw_content, meta_json").eq(
                "story_id", project_id
            )
            if arc_id is not None or (scope_sequential and past_arc_ids is not None):
                arc_ids = list(past_arc_ids or [])
                if arc_id:
                    arc_ids.append(arc_id)
                if arc_ids:
                    q = q.in_("arc_id", arc_ids)
            if query and query.strip():
                pattern = "%" + str(query).strip() + "%"
                q = q.ilike("content", pattern)
            r = q.limit(max(top_k, 20)).execute()
            return list(r.data) if r.data else []
        except Exception:
            return []
