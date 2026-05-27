"""The running host process for Johnny 5.

`johnny` is the ASGI host: it builds the FastAPI app, owns the startup/shutdown
lifespan that opens and closes shared resources, mounts the API and (later) the
WebSocket consciousness/state surfaces, and starts the cognitive cycle. It wires
`foundation`, `core`, and `brain` together; it is the only layer permitted to
import all three.
"""
