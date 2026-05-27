"""``POST /api/v1/input`` — the "talk to him" send (``SPEC §7``).

This is the **only** write the web API does, and it does NOT bypass the cognitive
cycle (FC-1/FC-9): it pushes the message onto the same Redis ``InputQueue`` the
REPL uses, which Sensorium drains on its next tick → the message becomes a
high-salience percept → Johnny appraises, recalls, and narrates it → his reply is
a thought on ``/ws/consciousness``. There is **no synchronous reply** here; the
endpoint only acknowledges that the message was enqueued.

Three guards keep a runaway client from harming the Mind:

* **blank** input → 422 (Pydantic validator);
* **oversized** input → 413 (``web_input_max_chars``);
* a **full queue** → 429 (``web_input_max_queue_depth``) — back-pressure so the
  Redis list can't grow unboundedly faster than the cycle drains it.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from foundation.config import Settings
from johnny.api.v1.deps import RuntimeDep
from johnny.api.v1.schemas import InputAccepted, InputRequest

# Sensorium tags inputs by provenance; the web doorway is always "web".
_INPUT_SOURCE = "web"

router = APIRouter(tags=["input"])


@router.post("/input", response_model=InputAccepted, status_code=status.HTTP_202_ACCEPTED)
async def post_input(body: InputRequest, request: Request, runtime: RuntimeDep) -> InputAccepted:
    """Enqueue a message for Johnny to perceive next tick; return the queue depth."""
    settings: Settings = request.app.state.settings

    if len(body.text) > settings.web_input_max_chars:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"text exceeds {settings.web_input_max_chars} characters",
        )

    queue = runtime.input_queue
    if await queue.depth() >= settings.web_input_max_queue_depth:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="input queue is full — Johnny hasn't caught up yet, retry shortly",
        )

    await queue.push(body.text, source=_INPUT_SOURCE)
    return InputAccepted(accepted=True, queue_depth=await queue.depth())
