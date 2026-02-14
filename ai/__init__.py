# ai/ - Package tách từ ai_engine (Router, Context, Service, helpers)
from ai.service import AIService, _get_default_tool_model
from ai.context_helpers import (
    get_mandatory_rules,
    resolve_chapter_range,
    get_entity_relations,
)
from ai.router import SmartAIRouter
from ai.evaluate import evaluate_step_outcome, replan_after_step
from ai.content import (
    suggest_relations,
    suggest_import_category,
    generate_arc_summary_from_chapters,
    generate_chapter_metadata,
    extract_timeline_events_from_content,
    get_file_sample,
    analyze_split_strategy,
    execute_split_logic,
)
from ai.rule_mining import RuleMiningSystem

__all__ = [
    "AIService",
    "_get_default_tool_model",
    "get_mandatory_rules",
    "resolve_chapter_range",
    "get_entity_relations",
    "SmartAIRouter",
    "evaluate_step_outcome",
    "replan_after_step",
    "suggest_relations",
    "suggest_import_category",
    "generate_arc_summary_from_chapters",
    "generate_chapter_metadata",
    "extract_timeline_events_from_content",
    "get_file_sample",
    "analyze_split_strategy",
    "execute_split_logic",
    "RuleMiningSystem",
]
