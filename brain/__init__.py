"""The Mind — Johnny's self-modifiable cognition (SPEC §9.0).

`brain/` holds the cognitive cycle, the society of inner agents, the LLM router,
and (later) the Conscience and self-model. It is fully self-modifiable subject to
the self-modification tiers (SPEC §9.2). It depends on `foundation` for infra but
must never import-mutate `core/` — that one-way isolation is the project's
defining invariant (FC-1).
"""
