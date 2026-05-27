"""Metacognition — Johnny thinking about how he's functioning (``SPEC §5`` #12).

Each sleep, Metacognition reviews recent outcomes (goals resolved vs abandoned,
degraded ticks, drive/mood patterns) and writes a first-person review plus
zero-or-more **self-improvement proposals** to ``self_improvement_note``.

It **proposes only** — applying a proposal (editing a prompt, retuning a drive,
changing code) is the Phase-9 gated self-edit flow, never here. Notes are written
with ``status="open"`` and are informational this phase; nothing reads them to act.
"""
