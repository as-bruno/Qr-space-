"""
Project Brain Package
=====================
Exposes the core public API so downstream code can do:

    from brain import orchestrator, intent_parser, agent_bridge
    from brain.orchestrator import upsert_entity, enqueue_tool_request
"""

from . import orchestrator
from . import intent_parser
from . import agent_bridge

__all__ = ["orchestrator", "intent_parser", "agent_bridge"]
__version__ = "1.0.0"
