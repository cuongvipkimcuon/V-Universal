# ai/hybrid_search.py - HybridSearch, check_semantic_intent, search_chunks_vector
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from config import init_services

from ai.service import AIService
from ai.context_helpers import get_archived_bible_ids
from ai.utils import (
    _safe_float,
    _rerank_by_score,
    _rerank_by_score_with_breakdown,
    _rerank_by_score_with_prefix,
)


class HybridSearch:
    """Hệ thống tìm kiếm kết hợp vector và từ khóa (V5: re-ranking, lookup_count, last_lookup_at)"""

    @staticmethod
    def smart_search_hybrid_raw(
        query_text: str,
        project_id: str,
        top_k: int = 10,
        inferred_prefixes: Optional[List[str]] = None,
    ) -> List[Dict]:
        try:
            services = init_services()
            supabase = services["supabase"]
            query_vec = AIService.get_embedding(query_text)
            candidate_limit = max(top_k * 3, 30)

            if query_vec:
                try:
                    response = supabase.rpc("hybrid_search", {
                        "query_text": query_text,
                        "query_embedding": query_vec,
                        "match_threshold": 0.3,
                        "match_count": candidate_limit,
                        "story_id_input": project_id,
                    }).execute()
                    raw_list = response.data if response.data else []
                except Exception:
                    raw_list = []
                if not raw_list:
                    try:
                        response = supabase.table("story_bible").select("*").eq(
                            "story_id", project_id
                        ).or_(f"entity_name.ilike.%{query_text}%,description.ilike.%{query_text}%").limit(
                            candidate_limit
                        ).execute()
                        raw_list = response.data if response.data else []
                        for item in raw_list:
                            item["similarity"] = 0.5
                    except Exception:
                        raw_list = []
            else:
                try:
                    response = supabase.table("story_bible").select("*").eq(
                        "story_id", project_id
                    ).or_(f"entity_name.ilike.%{query_text}%,description.ilike.%{query_text}%").limit(
                        candidate_limit
                    ).execute()
                    raw_list = response.data if response.data else []
                    for item in raw_list:
                        item["similarity"] = 0.5
                except Exception:
                    raw_list = []

            if not raw_list:
                return []

            # V7.7: Loại entry đã archived (không đưa vào context)
            try:
                archived_ids = get_archived_bible_ids(project_id)
                if archived_ids:
                    raw_list = [r for r in raw_list if r.get("id") not in archived_ids]
            except Exception:
                pass

            if inferred_prefixes:
                reranked = _rerank_by_score_with_prefix(raw_list, top_k, inferred_prefixes)
            else:
                reranked = _rerank_by_score(raw_list, top_k)
            return reranked

        except Exception as e:
            print(f"Search error: {e}")
            return []

    @staticmethod
    def smart_search_hybrid_raw_with_scores(query_text: str, project_id: str, top_k: int = 10) -> List[Dict]:
        try:
            services = init_services()
            supabase = services["supabase"]
            query_vec = AIService.get_embedding(query_text)
            candidate_limit = max(top_k * 3, 30)
            if query_vec:
                try:
                    response = supabase.rpc("hybrid_search", {
                        "query_text": query_text,
                        "query_embedding": query_vec,
                        "match_threshold": 0.3,
                        "match_count": candidate_limit,
                        "story_id_input": project_id,
                    }).execute()
                    raw_list = response.data if response.data else []
                except Exception:
                    raw_list = []
                if not raw_list:
                    try:
                        response = supabase.table("story_bible").select("*").eq(
                            "story_id", project_id
                        ).or_(f"entity_name.ilike.%{query_text}%,description.ilike.%{query_text}%").limit(
                            candidate_limit
                        ).execute()
                        raw_list = response.data if response.data else []
                        for item in raw_list:
                            item["similarity"] = 0.5
                    except Exception:
                        raw_list = []
            else:
                try:
                    response = supabase.table("story_bible").select("*").eq(
                        "story_id", project_id
                    ).or_(f"entity_name.ilike.%{query_text}%,description.ilike.%{query_text}%").limit(
                        candidate_limit
                    ).execute()
                    raw_list = response.data if response.data else []
                    for item in raw_list:
                        item["similarity"] = 0.5
                except Exception:
                    raw_list = []
            if not raw_list:
                return []
            return _rerank_by_score_with_breakdown(raw_list, top_k)
        except Exception as e:
            print(f"Search error: {e}")
            return []

    @staticmethod
    def update_lookup_stats(entity_id: Any) -> None:
        if entity_id is None:
            return
        try:
            services = init_services()
            if not services:
                return
            supabase = services["supabase"]
            now_iso = datetime.now(timezone.utc).isoformat()
            try:
                row = supabase.table("story_bible").select("lookup_count").eq("id", entity_id).execute()
                current = 0
                if row.data and len(row.data) > 0:
                    current = _safe_float(row.data[0].get("lookup_count"), 0.0)
                new_count = int(current) + 1
                supabase.table("story_bible").update({
                    "lookup_count": new_count,
                    "last_lookup_at": now_iso,
                }).eq("id", entity_id).execute()
            except Exception:
                pass
        except Exception as e:
            print(f"update_lookup_stats error: {e}")

    @staticmethod
    def smart_search_hybrid(query_text: str, project_id: str, top_k: int = 10) -> str:
        raw_data = HybridSearch.smart_search_hybrid_raw(query_text, project_id, top_k)
        results = []
        if raw_data:
            for item in raw_data:
                name = item.get("entity_name") or ""
                desc = item.get("description") or ""
                results.append(f"- [{name}]: {desc}")
        return "\n".join(results) if results else ""


def check_semantic_intent(
    query_text: str,
    project_id: str,
    threshold: float = 0.90,
) -> Optional[Dict]:
    if not query_text or not project_id:
        return None
    try:
        services = init_services()
        if not services:
            return None
        supabase = services["supabase"]
        try:
            supabase.table("semantic_intent").select("id").limit(1).execute()
        except Exception:
            return None
        try:
            r = supabase.table("settings").select("value").eq("key", "semantic_intent_threshold").execute()
            if r.data and r.data[0]:
                t = r.data[0].get("value")
                threshold = max(0.85, min(1.0, float(t) / 100.0)) if t is not None else threshold
        except Exception:
            pass
        query_vec = AIService.get_embedding(query_text)
        if not query_vec:
            return None
        rows = supabase.table("semantic_intent").select("id, question_sample, intent, related_data, embedding").eq("story_id", project_id).execute()
        data = rows.data or []
        best_match = None
        best_sim = 0.0
        for row in data:
            emb = row.get("embedding")
            if emb is None:
                continue
            if isinstance(emb, str):
                try:
                    emb = json.loads(emb)
                except Exception:
                    continue
            try:
                import math
                dot = sum(a * b for a, b in zip(query_vec, emb))
                na = math.sqrt(sum(a * a for a in query_vec))
                nb = math.sqrt(sum(b * b for b in emb))
                sim = dot / (na * nb) if na and nb else 0
                sim = (sim + 1) / 2
                if sim >= threshold and sim > best_sim:
                    best_sim = sim
                    best_match = {**row, "similarity": sim}
            except Exception:
                pass
        return best_match
    except Exception as e:
        print(f"check_semantic_intent error: {e}")
        return None


def search_chunks_vector(
    query_text: str,
    project_id: str,
    arc_id: Optional[str] = None,
    top_k: int = 10,
) -> List[Dict]:
    try:
        services = init_services()
        if not services:
            return []
        supabase = services["supabase"]
        query_vec = AIService.get_embedding(query_text)
        q = supabase.table("chunks").select("id, chapter_id, arc_id, content, raw_content, meta_json, story_id").eq("story_id", project_id)
        if arc_id:
            q = q.eq("arc_id", arc_id)
        if query_vec:
            try:
                r = supabase.rpc("hybrid_chunk_search", {
                    "query_text": query_text,
                    "query_embedding": query_vec,
                    "story_id_input": project_id,
                    "match_threshold": 0.3,
                    "match_count": top_k,
                }).execute()
                rows = list(r.data) if r.data else []
                if arc_id and not rows and query_text and query_text.strip():
                    rows = search_chunks_vector(query_text, project_id, arc_id=None, top_k=top_k)
                return rows
            except Exception:
                pass
        if query_text and query_text.strip():
            pattern = "%" + str(query_text).strip() + "%"
            r = q.ilike("content", pattern).limit(top_k).execute()
            rows = list(r.data) if r.data else []
            if arc_id and not rows:
                rows = search_chunks_vector(query_text, project_id, arc_id=None, top_k=top_k)
            return rows
        return []
    except Exception as e:
        print(f"search_chunks_vector error: {e}")
        return []
