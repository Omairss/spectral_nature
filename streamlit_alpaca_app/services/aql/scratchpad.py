"""Backward-compatible shim for the agent scratchpad.

The implementation lives in services.agents.scratchpad. New code should import
from services.agents.
"""
from __future__ import annotations

from services.agents.scratchpad import *  # noqa: F401,F403
