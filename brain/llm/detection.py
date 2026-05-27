"""Object detection — YOLO11 on inference.lan:8003.

Verified contract: ``POST /detect/base64 {"image": "<base64>"}`` →
``{"detections": [{"class": str, "confidence": float,
"bbox": {"x1","y1","x2","y2"}}], "count": int, "image_size": {...}}``. Used from
Phase 2 onward for visual percepts; the client ships now with the rest of the
inference substrate.
"""

from __future__ import annotations

import base64

import httpx
from pydantic import BaseModel

from brain.llm.base import ProviderError, ProviderTimeoutError
from foundation.config import Settings

DETECTOR_PROVIDER_NAME = "yolo"
_DEFAULT_TIMEOUT = 60.0


class BoundingBox(BaseModel):
    x1: float
    y1: float
    x2: float
    y2: float


class Detection(BaseModel):
    label: str
    confidence: float
    bbox: BoundingBox


def parse_detections(data: dict[str, object]) -> list[Detection]:
    """Project a ``/detect`` envelope into a list of ``Detection``.

    Pure (no I/O) so contract tests can feed a captured fixture through it. Maps
    the server's ``class`` field to ``label``.
    """
    detections = data.get("detections") or []
    if not isinstance(detections, list):
        raise ProviderError(
            "YOLO server returned a non-list 'detections'", provider=DETECTOR_PROVIDER_NAME
        )
    return [
        Detection(
            label=item["class"],
            confidence=item["confidence"],
            bbox=BoundingBox(**item["bbox"]),
        )
        for item in detections
    ]


class Detector:
    def __init__(self, settings: Settings) -> None:
        self._client = httpx.AsyncClient(
            base_url=settings.yolo_base_url.rstrip("/"),
            timeout=_DEFAULT_TIMEOUT,
        )

    async def detect(self, image: bytes) -> list[Detection]:
        """Detect objects in raw image bytes."""
        return await self.detect_base64(base64.b64encode(image).decode())

    async def detect_base64(self, image_b64: str) -> list[Detection]:
        """Detect objects from an already-base64-encoded image."""
        try:
            resp = await self._client.post("/detect/base64", json={"image": image_b64})
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                "YOLO server timed out", provider=DETECTOR_PROVIDER_NAME
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                f"YOLO transport error: {exc}", provider=DETECTOR_PROVIDER_NAME
            ) from exc

        if resp.status_code >= 400:
            raise ProviderError(
                f"YOLO server returned HTTP {resp.status_code}: {resp.text[:200]}",
                provider=DETECTOR_PROVIDER_NAME,
                status_code=resp.status_code,
            )

        data = resp.json()
        return [
            Detection(
                label=item["class"],
                confidence=item["confidence"],
                bbox=BoundingBox(**item["bbox"]),
            )
            for item in data.get("detections", [])
        ]

    async def aclose(self) -> None:
        await self._client.aclose()
