"""Shared infrastructure for Johnny 5.

`foundation` holds the substrate that every other package builds on — settings,
the async database engine, the Redis client, and observability. It depends on no
other internal package, so both the immutable Core and the self-modifiable Mind
can import it without crossing the Core/Mind trust boundary (SPEC §9.0 / FC-1).
"""
