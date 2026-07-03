"""Agent and chat/search namespace.

This package exposes shared chat, scratchpad, and Zopedia agent utilities.
Feature code should import canonical service modules directly when possible.
"""
from __future__ import annotations

from importlib import import_module
from typing import Any

from services.agents.chat_log import (
    append_chat_message,
    bootstrap_chat_log,
    count_chat_sessions,
    create_chat_thread,
    list_chat_sessions,
    list_chat_threads,
    load_chat_session,
    load_chat_thread,
    log_chat_session,
)
from services.agents.scratchpad import (
    clear as clear_scratchpad,
    read_entries,
    read_summary,
    write_entry,
)
from services.common.hypothesis import verify_hypothesis

_SHARED_SUBMODULES = {
    "agent_tools",
    "append_chat_message",
    "zopedia_resolver",
    "zopedia_agent",
    "zopedia_research",
    "page_browsing",
    "web_research",
}


def __getattr__(name: str) -> Any:
    if name in _SHARED_SUBMODULES:
        module = import_module(f"services.{name}")
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "agent_tools",
    "zopedia_resolver",
    "zopedia_agent",
    "zopedia_research",
    "page_browsing",
    "bootstrap_chat_log",
    "clear_scratchpad",
    "count_chat_sessions",
    "create_chat_thread",
    "list_chat_sessions",
    "list_chat_threads",
    "load_chat_session",
    "load_chat_thread",
    "log_chat_session",
    "read_entries",
    "read_summary",
    "verify_hypothesis",
    "web_research",
    "write_entry",
]
