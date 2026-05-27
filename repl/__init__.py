"""The REPL cockpit — a terminal window into the running Johnny.

A standalone process that attaches to the headless Mind over Redis: it tails the
stream of consciousness, dumps the current workspace, injects input, and
pauses/steps the heartbeat. It is an *interface* (Layer 1), not an inner agent —
it reaches Johnny only through the workspace bus, the input queue, and the cycle
control channel, exactly as voice and the web UI will.
"""

from __future__ import annotations

from repl.cockpit import Cockpit

__all__ = ["Cockpit"]
