"""Contract tests for the YOLO11 detection client.

The Detector projects the server's ``{"detections":[{"class",...,"bbox":{...}}]}``
envelope into ``Detection`` models (notably server ``class`` → ``Detection.label``).
A literal captured envelope (``yolo_bus_detections.json`` — real YOLO output for
ultralytics bus.jpg) is fed through ``detect_base64`` over httpx MockTransport and
the projection is asserted. No network.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from brain.llm.base import ProviderError
from brain.llm.detection import DETECTOR_PROVIDER_NAME, Detection, Detector
from foundation.config import Settings

pytestmark = pytest.mark.contract

FixtureLoader = Callable[[str], Any]
YOLO = "llm/yolo_bus_detections.json"


def _detector_with(handler: Callable[[httpx.Request], httpx.Response]) -> Detector:
    detector = Detector(Settings())
    # Replace the real client (constructed in __init__) with a mocked transport.
    detector._client = httpx.AsyncClient(
        base_url="http://test", transport=httpx.MockTransport(handler)
    )
    return detector


async def test_projects_real_envelope_to_detections(load_fixture: FixtureLoader) -> None:
    env = load_fixture(YOLO)
    detector = _detector_with(lambda req: httpx.Response(200, json=env))
    try:
        detections = await detector.detect_base64("ignored")
    finally:
        await detector.aclose()

    assert len(detections) == env["count"] == 5
    assert all(isinstance(d, Detection) for d in detections)
    labels = {d.label for d in detections}
    assert labels == {"bus", "person"}  # server "class" -> Detection.label

    bus = next(d for d in detections if d.label == "bus")
    assert 0.0 < bus.confidence <= 1.0
    # bbox projected with all four corners, x2>x1 and y2>y1.
    assert bus.bbox.x2 > bus.bbox.x1
    assert bus.bbox.y2 > bus.bbox.y1


async def test_empty_detections_returns_empty_list() -> None:
    detector = _detector_with(
        lambda req: httpx.Response(200, json={"detections": [], "count": 0, "image_size": {}})
    )
    try:
        assert await detector.detect_base64("ignored") == []
    finally:
        await detector.aclose()


async def test_http_error_raises_provider_error() -> None:
    detector = _detector_with(lambda req: httpx.Response(503, text="model loading"))
    try:
        with pytest.raises(ProviderError) as exc_info:
            await detector.detect_base64("ignored")
        assert exc_info.value.status_code == 503
        assert exc_info.value.provider == DETECTOR_PROVIDER_NAME
    finally:
        await detector.aclose()
