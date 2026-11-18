import asyncio
import os
import sys

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import wavespeed


@pytest.mark.parametrize(
    ("model", "image_url"),
    [
        ("i2v-480p", "https://media.wavespeed.ai/source.png"),
        ("t2v-480p", None),
    ],
)
def test_video_options_are_forwarded_to_wavespeed(monkeypatch, model, image_url):
    monkeypatch.setenv("WAVESPEED_API_KEY", "test-token")
    response = httpx.Response(200, json={"data": {"id": "pred_123", "status": "created"}})
    calls = []

    def submit(*args, **kwargs):
        calls.append((args, kwargs))
        return response

    monkeypatch.setattr(wavespeed.httpx, "post", submit)
    monkeypatch.setattr(wavespeed.db, "one", lambda *args, **kwargs: {"prediction_id": "pred_123"})

    asset = {
        "type": "video",
        "model": f"wavespeed-ai/wan-2.1/{model}",
        "prompt": "Animate the image with a slow camera push-in",
        "duration": 10,
        "size": "480*832",
        "negative_prompt": "blur, jitter",
    }
    if image_url:
        asset["image_url"] = image_url
    row = wavespeed.submit_generation(7, 9, asset)

    assert row["prediction_id"] == "pred_123"
    body = calls[0][1]["json"]
    assert calls[0][0][0].endswith(f"/wavespeed-ai/wan-2.1/{model}")
    assert body["duration"] == 10
    assert body["size"] == "480*832"
    assert body["negative_prompt"] == "blur, jitter"
    assert body.get("image") == image_url


def test_image_to_video_requires_https_image_url(monkeypatch):
    monkeypatch.setenv("WAVESPEED_API_KEY", "test-token")
    submitted = False

    def reject_submit(*args, **kwargs):
        nonlocal submitted
        submitted = True
        raise AssertionError("Provider submission should not be reached")

    monkeypatch.setattr(wavespeed.httpx, "post", reject_submit)
    with pytest.raises(wavespeed.WaveSpeedError, match="source image"):
        wavespeed.submit_generation(7, 9, {
            "type": "video",
            "model": "wavespeed-ai/wan-2.1/i2v-480p",
            "prompt": "Animate this",
            "image_url": "http://example.com/image.png",
        })
    assert submitted is False


@pytest.mark.parametrize(
    ("field", "value"),
    [("duration", 7), ("size", "1024*1024"), ("negative_prompt", "x" * 1001)],
)
def test_video_rejects_unsupported_options(monkeypatch, field, value):
    monkeypatch.setenv("WAVESPEED_API_KEY", "test-token")

    def reject_submit(*args, **kwargs):
        raise AssertionError("Invalid request should not reach WaveSpeed")

    monkeypatch.setattr(wavespeed.httpx, "post", reject_submit)
    asset = {"type": "video", "prompt": "test", field: value}
    with pytest.raises(wavespeed.WaveSpeedError):
        wavespeed.submit_generation(7, 9, asset)


def test_upload_uses_wave_speed_ticket_and_does_not_send_key_to_storage(monkeypatch):
    monkeypatch.setenv("WAVESPEED_API_KEY", "test-token")
    requests = []
    client_class = httpx.AsyncClient

    async def handler(request):
        requests.append(request)
        if request.url.path.endswith("/media/uploads"):
            assert request.headers["authorization"] == "Bearer test-token"
            return httpx.Response(200, json={"data": {
                "download_url": "https://media.wavespeed.ai/source.png",
                "upload": {
                    "method": "PUT",
                    "url": "https://storage.example/upload-ticket",
                    "headers": {"Content-Type": "image/png"},
                },
            }})
        assert request.method == "PUT"
        assert "authorization" not in request.headers
        assert request.content == b"\x89PNG\r\n\x1a\nimage"
        return httpx.Response(200)

    def mock_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return client_class(*args, **kwargs)

    monkeypatch.setattr(wavespeed.httpx, "AsyncClient", mock_client)
    result = asyncio.run(wavespeed._upload_image_to_wavespeed(
        "source.png", "image/png", b"\x89PNG\r\n\x1a\nimage"))

    assert result == "https://media.wavespeed.ai/source.png"
    assert len(requests) == 2
