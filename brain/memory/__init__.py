"""Johnny's memory spine — four stores plus consolidation.

Working memory is Redis-backed (bounded, decaying); episodic, semantic, and
procedural memory are Postgres + pgvector. Recall is hybrid (similarity ×
recency × salience), never pure vector. All persistent access goes through the
repositories here, which sit on the Phase 0 ``Repository``/``session_scope``
foundation and embed exclusively via the Phase 0 ``Embedder``.
"""

from __future__ import annotations

# The embedding width every store persists and recalls against (BGE-M3). Kept in
# lockstep with ``Settings.embed_dimensions`` and the migration's vector columns.
EMBED_DIM = 1024
