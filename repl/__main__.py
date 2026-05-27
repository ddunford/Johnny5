"""Entry point: ``python -m repl`` (or ``./ctl.sh repl``) opens the cockpit.

Configures logging to a quiet level so the structured app logs don't drown the
consciousness feed, then runs the cockpit against the same Redis the headless
runtime uses.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from foundation.config import get_settings
from foundation.observability import configure_logging
from repl.cockpit import Cockpit


def main() -> None:
    settings = get_settings()
    # Keep the cockpit readable: only warnings+ from the app's structured logger.
    configure_logging(settings)
    logging.getLogger().setLevel(logging.WARNING)
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(Cockpit().run())


if __name__ == "__main__":
    main()
