# views - UI render functions
from .sidebar import render_sidebar
from .dashboard import render_dashboard_tab
from .chat import render_chat_tab
from .workstation import render_workstation_tab
from .bible import render_bible_tab
from .cost import render_cost_tab
from .settings import render_settings_tab
from .collaboration import render_collaboration_tab
from .setup_tabs import render_prefix_setup, render_persona_setup

__all__ = [
    "render_sidebar",
    "render_dashboard_tab",
    "render_chat_tab",
    "render_workstation_tab",
    "render_bible_tab",
    "render_cost_tab",
    "render_settings_tab",
    "render_collaboration_tab",
    "render_prefix_setup",
    "render_persona_setup",
]
