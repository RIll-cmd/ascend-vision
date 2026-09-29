"""SDK contract for direct discipline writes."""
from datetime import datetime, timezone
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from vision_client.ascend_core_client import AscendCoreVisionClient


def test_record_discipline_event_posts_scoped_payload():
    client = AscendCoreVisionClient("http://core.test", "token", "character-1", "camera-1")
    response = MagicMock()
    response.json.return_value = {"success": True}
    transport = AsyncMock()
    transport.post.return_value = response
    context = AsyncMock()
    context.__aenter__.return_value = transport
    with patch("vision_client.ascend_core_client.httpx.AsyncClient", return_value=context):
        result = asyncio.run(client.record_discipline_event(
            event_id="event-1", behavior="PHONE_USE", stage="PENALTY",
            reason="Phone detected again", observed_at=datetime(2026, 9, 13, tzinfo=timezone.utc),
        ))
    assert result == {"success": True}
    args, kwargs = transport.post.await_args
    assert args[0] == "/api/integration/vision/discipline-events"
    assert kwargs["json"]["characterId"] == "character-1"
    assert kwargs["json"]["deviceId"] == "camera-1"
    assert kwargs["json"]["stage"] == "PENALTY"
    assert kwargs["headers"]["Authorization"] == "Bearer token"


def test_record_discipline_event_rejects_naive_timestamp():
    client = AscendCoreVisionClient("http://core.test", "token", "character-1")
    with pytest.raises(ValueError, match="timezone-aware"):
        asyncio.run(client.record_discipline_event(
            event_id="event-1", behavior="PHONE_USE", stage="WARNING",
            reason="Phone detected", observed_at=datetime(2026, 9, 13),
        ))
