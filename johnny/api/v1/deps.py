"""Request-scoped dependencies for the ``/api/v1`` routes.

The HTTP layer is a *consumer* of the headless Mind (FC-8): the cognitive runtime
owns the workspace, drives, affect, goals, sleep, and the read stores, and is
attached to ``app.state.runtime`` by the lifespan. These dependencies hand a route
the live runtime (or a 503 if the Mind isn't attached — which shouldn't happen
under the lifespan, but a read endpoint degrades rather than 500s).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, Request, status
from fastapi import HTTPException as _HTTPException


def get_runtime(request: Request) -> Any:
    """The live ``CognitiveRuntime`` off ``app.state`` (503 when the Mind is detached).

    Typed as ``Any`` so the dependency works against both the production
    ``CognitiveRuntime`` and the lightweight namespaces test apps attach; the route
    handlers access only the documented read surface (``workspace``, ``drives``,
    ``affect``, ``goals``, ``sleep``, ``episodic``, ``semantic``, ``identity``,
    ``metacognition``, ``action_audit``, ``notes``, ``cycle``, ``input_queue``).
    """
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None:
        raise _HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="the Mind is not attached",
        )
    return runtime


RuntimeDep = Annotated[Any, Depends(get_runtime)]
