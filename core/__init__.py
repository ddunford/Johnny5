"""The Core — Johnny's immutable trust boundary (SPEC §9.0 / FC-1).

The Core protects continuity and the host: kill switch, append-only audit log,
budget/resource governors, integrity gate, identity anchor. It is read-only to
the running Mind — nothing in `brain/` or `tools/` may import-mutate anything
here, and the Core never judges thought or goal *content* (FC-9). Treat this as
a separate trust boundary, not "a folder we lock down later".
"""
