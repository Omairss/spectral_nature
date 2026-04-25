"""Agent and chat/search namespace.

This package is the stable import seam for omnibar, agent tool, and
research workflow APIs while the underlying modules are migrated out of
their legacy locations.
"""
from __future__ import annotations

from services import agent_tools as agent_tools  # noqa: F401
from services import omnibar as omnibar  # noqa: F401
from services import omnibar_agent as omnibar_agent  # noqa: F401
from services import omnibar_research as omnibar_research  # noqa: F401
from services import page_browsing as page_browsing  # noqa: F401
from services import web_research as web_research  # noqa: F401

__all__ = [
    "agent_tools",
    "omnibar",
    "omnibar_agent",
    "omnibar_research",
    "page_browsing",
    "web_research",
]
