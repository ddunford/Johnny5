"""The Self-Model — Johnny's evolving self-concept (``SPEC §5`` #11, §12).

Who Johnny is *becoming*. The ``identity`` table is append-versioned: each sleep's
self-model refresh writes a new version (latest = current), grown **around** the
immutable Core identity anchor (name + prime directive, ``core/identity_anchor.py``)
— never into it (FC-1). The store owns persistence; the agent owns the reflection
that produces a new version through the ``self_model`` router role.
"""
