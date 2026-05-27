"""Johnny's society of inner agents.

Each inner agent is an autonomous module that reaches the rest of the society
only through the Global Workspace (``brain/workspace.py``) — it never calls
another agent directly (``SPEC §4/§5``). Every agent conforms to the
``InnerAgent`` protocol and is registered with the ``AgentRegistry`` so the set is
dynamic: Johnny can spawn or retire agents at runtime (FC-2 / Phase 9) without the
cognitive cycle being rewired. The three Phase-2 agents — Sensorium, Attention,
Inner Narrator — are the first members.
"""

from __future__ import annotations

from brain.agents.base import AgentRegistry, InnerAgent

__all__ = ["AgentRegistry", "InnerAgent"]
