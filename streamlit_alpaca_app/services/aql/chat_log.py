"""Backward-compatible shim for agent chat logging.

The implementation lives in services.agents.chat_log. New code should import
from services.agents.
"""
from __future__ import annotations

from services.agents.chat_log import *  # noqa: F401,F403
