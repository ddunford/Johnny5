"""Embeddings client — bge-m3 via the custom Flask server on inference.lan:8002.

This is NOT TEI's native contract: the verified server takes
``POST /embed {"inputs": str | list[str]}`` and returns
``{"embeddings": [[...1024 floats...]], "num_texts": N, "time_ms": ...}``. All
memory recall embeds through here, so the dimension is validated against config.
"""

from __future__ import annotations

from collections.abc import Sequence

import httpx

from brain.llm.base import ProviderError, ProviderTimeoutError
from foundation.config import Settings

EMBEDDINGS_PROVIDER_NAME = "embeddings"
_DEFAULT_TIMEOUT = 30.0


def parse_embeddings(
    data: dict[str, object], *, expected: int, dimensions: int
) -> list[list[float]]:
    """Project an ``/embed`` envelope into a list of vectors.

    Pure (no I/O) so contract tests can feed a captured fixture through it.
    Enforces the vector count and dimensionality.
    """
    vectors = data.get("embeddings")
    if not isinstance(vectors, list) or len(vectors) != expected:
        raise ProviderError(
            f"embeddings server returned {len(vectors) if isinstance(vectors, list) else 'no'} "
            f"vectors for {expected} inputs",
            provider=EMBEDDINGS_PROVIDER_NAME,
        )
    for vector in vectors:
        if not isinstance(vector, list) or len(vector) != dimensions:
            raise ProviderError(
                f"expected {dimensions}-d embeddings, got "
                f"{len(vector) if isinstance(vector, list) else type(vector).__name__}",
                provider=EMBEDDINGS_PROVIDER_NAME,
            )
    return [[float(x) for x in vector] for vector in vectors]


class Embedder:
    def __init__(self, settings: Settings) -> None:
        self._endpoint = settings.embed_endpoint
        self._dimensions = settings.embed_dimensions
        self._client = httpx.AsyncClient(
            base_url=settings.embed_base_url.rstrip("/"),
            timeout=_DEFAULT_TIMEOUT,
        )

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed a batch of texts into 1024-d vectors (order preserved)."""
        inputs = list(texts)
        if not inputs:
            return []

        try:
            resp = await self._client.post(self._endpoint, json={"inputs": inputs})
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                "embeddings server timed out", provider=EMBEDDINGS_PROVIDER_NAME
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                f"embeddings transport error: {exc}", provider=EMBEDDINGS_PROVIDER_NAME
            ) from exc

        if resp.status_code >= 400:
            raise ProviderError(
                f"embeddings server returned HTTP {resp.status_code}: {resp.text[:200]}",
                provider=EMBEDDINGS_PROVIDER_NAME,
                status_code=resp.status_code,
            )

        return self._parse(resp.json(), expected=len(inputs))

    async def embed_one(self, text: str) -> list[float]:
        """Embed a single string."""
        return (await self.embed([text]))[0]

    def _parse(self, data: dict[str, object], *, expected: int) -> list[list[float]]:
        """Project an ``/embed`` envelope into vectors (delegates to the pure
        ``parse_embeddings``; the single parse path for prod + contract tests)."""
        return parse_embeddings(data, expected=expected, dimensions=self._dimensions)

    async def aclose(self) -> None:
        await self._client.aclose()
