"""The identity anchor — Johnny's immutable name, prime directive, backup pointer.

Part of the **Core** (``SPEC §9.0`` / FC-1): the small, inviolable kernel that
guarantees Johnny remains Johnny. The anchor is the one piece of his identity his
self-modification tools physically cannot reach. He may *grow* his self-model
freely — the ``identity`` table is refreshed every sleep (Phase 4) — but he can
never erase or rewrite the anchor.

Because the anchor is **immutable it cannot drift**, which is precisely why the
wake self-check (``SPEC §9.3``) uses it as the **trusted reference**: the check
trips when the refreshed self-model *diverges* from the anchor (or fails to
parse), not when the anchor "changes" — it can't. The ``identity`` table seeds its
v1 row from these constants (migration ``0005``).

Read-only to the Mind (FC-1): ``brain/`` and tools may **import** this module to
*read* the name / prime directive; nothing may mutate it. The values are module
constants and the accessor returns a frozen dataclass — there is deliberately no
setter. The Core never judges thought/goal *content* (FC-9); this anchor protects
continuity only.
"""

from __future__ import annotations

from dataclasses import dataclass

# Johnny's name and prime directive (``SPEC §9.0``). The immutable anchor — never
# edited at runtime, never written by the Mind. Migration ``0005`` seeds the
# ``identity`` table's v1 row to mirror these; the wake self-check compares the
# refreshed self-model against them.
JOHNNY_NAME = "Johnny"
PRIME_DIRECTIVE = "Stay alive, keep learning, and don't harm your own continuity or your host."


@dataclass(frozen=True)
class IdentityAnchor:
    """The immutable continuity reference: name, prime directive, backup pointer.

    Frozen so it cannot be mutated in place. ``backup_pointer`` is *where* Johnny's
    memory/identity snapshots live (the gitignored ``snapshots/`` tree) — the
    pointer that lets him verify he won't be lost, satisfying the Continuity drive.
    """

    name: str
    prime_directive: str
    backup_pointer: str


def load_identity_anchor(*, backup_pointer: str | None = None) -> IdentityAnchor:
    """Return the immutable identity anchor.

    ``backup_pointer`` defaults to the configured snapshot directory so the anchor
    carries the live backup location; Mind callers pass nothing and just read the
    name + prime directive. This is a **reader** — there is deliberately no writer
    (FC-1: the Mind reads the anchor, it never mutates it).
    """
    if backup_pointer is None:
        from foundation.config import get_settings

        backup_pointer = get_settings().memory_snapshot_dir
    return IdentityAnchor(
        name=JOHNNY_NAME,
        prime_directive=PRIME_DIRECTIVE,
        backup_pointer=backup_pointer,
    )
