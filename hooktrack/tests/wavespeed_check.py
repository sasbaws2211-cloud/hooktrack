import asyncio
import os
import sys
from datetime import date

import httpx
import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import wavespeed


@pytest.fixture(autouse=True)
def billing_disabled_for_provider_tests(monkeypatch):
    monkeypatch.setattr(wavespeed.billing, "reserve_ai", lambda _workspace_id: None)
    monkeypatch.setattr(wavespeed.billing, "release_ai", lambda _workspace_id, _month: None)
    monkeypatch.setattr(wavespeed.billing, "record_ai", lambda _workspace_id: None)


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


@pytest.mark.parametrize("num_images", [1, 2, 4])
def test_image_generation_requests_and_returns_variations(monkeypatch, num_images):
    monkeypatch.setenv("WAVESPEED_API_KEY", "test-token")
    urls = [f"https://media.wavespeed.ai/image-{index}.jpg" for index in range(num_images)]
    response = httpx.Response(200, json={"data": {
        "id": "pred_images",
        "status": "completed",
        "outputs": urls,
    }})
    calls = []

    def submit(*args, **kwargs):
        calls.append((args, kwargs))
        return response

    monkeypatch.setattr(wavespeed.httpx, "post", submit)
    monkeypatch.setattr(wavespeed.db, "one", lambda *args, **kwargs: {
        "prediction_id": "pred_images", "status": "completed", "output_url": urls[0],
    })

    row = wavespeed.submit_generation(7, 9, {
        "type": "image",
        "model": "wavespeed-ai/flux-dev",
        "prompt": "A test image",
        "num_images": num_images,
    })

    assert calls[0][1]["json"]["num_images"] == num_images
    assert row["output_url"] == urls[0]
    assert row["output_urls"] == urls


def test_image_variation_submission_uses_one_reserved_ai_request(monkeypatch):
    monkeypatch.setenv("WAVESPEED_API_KEY", "test-token")
    month = date(2026, 6, 1)
    reservations = []
    records = []
    monkeypatch.setattr(wavespeed.billing, "reserve_ai", lambda workspace_id: reservations.append(workspace_id) or month)
    monkeypatch.setattr(wavespeed.billing, "record_ai", lambda workspace_id: records.append(workspace_id))
    monkeypatch.setattr(wavespeed.httpx, "post", lambda *args, **kwargs:
                        httpx.Response(200, json={"data": {"id": "pred_images", "status": "created"}}))
    monkeypatch.setattr(wavespeed.db, "one", lambda *args, **kwargs: {"prediction_id": "pred_images"})

    wavespeed.submit_generation(7, 9, {
        "type": "image", "prompt": "A test image", "num_images": 4,
    })

    assert reservations == [7]
    assert records == []


def test_provider_rejection_releases_studio_ai_request(monkeypatch):
    monkeypatch.setenv("WAVESPEED_API_KEY", "test-token")
    month = date(2026, 6, 1)
    released = []
    monkeypatch.setattr(wavespeed.billing, "reserve_ai", lambda _workspace_id: month)
    monkeypatch.setattr(wavespeed.billing, "release_ai", lambda workspace_id, value: released.append((workspace_id, value)))
    monkeypatch.setattr(wavespeed.httpx, "post", lambda *args, **kwargs:
                        httpx.Response(402, json={"message": "Provider rejected request"}))

    with pytest.raises(wavespeed.WaveSpeedError):
        wavespeed.submit_generation(7, 9, {"type": "image", "prompt": "A test image"})

    assert released == [(7, month)]


def test_exhausted_studio_ai_quota_blocks_provider_submission(monkeypatch):
    def reject_quota(_workspace_id):
        raise HTTPException(402, "AI quota exhausted")

    monkeypatch.setattr(wavespeed.billing, "reserve_ai", reject_quota)
    monkeypatch.setattr(wavespeed.httpx, "post", lambda *args, **kwargs:
                        pytest.fail("Quota exhaustion must block provider submission"))

    with pytest.raises(HTTPException) as error:
        wavespeed.submit_generation(7, 9, {"type": "image", "prompt": "A test image"})

    assert error.value.status_code == 402


def test_image_to_video_quota_is_reserved_before_upload(monkeypatch):
    def reject_quota(_workspace_id):
        raise HTTPException(402, "AI quota exhausted")

    monkeypatch.setattr(wavespeed.billing, "reserve_ai", reject_quota)
    monkeypatch.setattr(wavespeed.db, "one", lambda *_args, **_kwargs: {"id": 9})
    monkeypatch.setattr(wavespeed, "_upload_image_to_wavespeed", lambda *_args, **_kwargs:
                        pytest.fail("Quota exhaustion must block source-image upload"))
    ctx = wavespeed.Ctx(user_id=1, email="test@example.com", name="Test",
                        workspace_id=7, role="member")

    with pytest.raises(HTTPException) as error:
        asyncio.run(wavespeed.generate_image_to_video(
            prompt="Animate this image", negative_prompt="", duration=5, size="832*480",
            post_id=9, file=None, ctx=ctx))

    assert error.value.status_code == 402


@pytest.mark.parametrize("num_images", [0, 5, "2", True])
def test_image_generation_rejects_invalid_variation_count(monkeypatch, num_images):
    monkeypatch.setenv("WAVESPEED_API_KEY", "test-token")

    def reject_submit(*args, **kwargs):
        raise AssertionError("Invalid request should not reach WaveSpeed")

    monkeypatch.setattr(wavespeed.httpx, "post", reject_submit)
    with pytest.raises(wavespeed.WaveSpeedError):
        wavespeed.submit_generation(7, 9, {
            "type": "image", "prompt": "test", "num_images": num_images,
        })


def test_polling_returns_all_safe_output_urls(monkeypatch):
    row = {"prediction_id": "pred_images", "status": "processing", "output_url": ""}
    urls = [
        "https://media.wavespeed.ai/one.jpg",
        "https://media.wavespeed.ai/two.jpg",
        "javascript:alert(1)",
    ]
    monkeypatch.setattr(wavespeed, "_fetch_prediction", lambda _: {"status": "completed", "outputs": urls})
    monkeypatch.setattr(wavespeed.db, "one", lambda *args, **kwargs: {
        "prediction_id": "pred_images", "status": "completed", "output_url": urls[0],
    })

    result = wavespeed.sync_generation(7, "pred_images")

    assert result["output_url"] == urls[0]
    assert result["output_urls"] == urls[:2]


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
