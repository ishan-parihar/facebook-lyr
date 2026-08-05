"""unit tests for facebook_mcp_server.scraping.graph_api.GraphApiClient."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from facebook_mcp_server.core.exceptions import (
    AuthenticationError,
    NetworkError,
    RateLimitError,
    ScrapingError,
)
from facebook_mcp_server.scraping.graph_api import GraphApiClient




def test_requires_token():
    with pytest.raises(AuthenticationError):
        GraphApiClient("   ")


def test_empty_token_is_rejected_even_with_cookie_client():
    with pytest.raises(AuthenticationError):
        GraphApiClient("  ", cookie_client=object())


class _Resp:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


class _FakeCookieClient:
    async def save_post(self, post_id):
        return {"success": True, "called": "save", "post_id": post_id}

    async def unsave_post(self, id):
        return {"success": True, "called": "unsave", "post_id": id}

    async def send_message(self, thread_id, body):
        return {
            "success": True,
            "called": "send",
            "thread_id": thread_id,
            "message_id": f"mid-{thread_id}",
        }

    async def delete_message(self, thread_id, message_id):
        return {
            "success": True,
            "called": "delete",
            "thread_id": thread_id,
            "message_id": message_id,
        }


@pytest.mark.asyncio
async def test_text_post_publishes_to_feed(monkeypatch):
    client = _sock()
    calls = []

    async def fake_request(method, path, *, token=None, data=None, multipart=None):
        calls.append((method, path))
        assert method == "POST"
        assert path == "/123/feed"
        assert data["message"] == "hello"
        assert data["published"] == "true"
        return {"id": "123_456"}

    client._graph_request = fake_request
    client._resolved_page_id = "123"
    client._resolved_page_token = "sock"

    result = await client.create_post("hello")
    assert result == {"success": True, "post_id": "123_456"}


@pytest.mark.asyncio
async def test_photo_post_returns_post_id_and_attachment_id(monkeypatch):
    client = _sock()
    client._resolved_page_id = "123"
    client._resolved_page_token = "sock"
    client._upload_photos = AsyncMock(return_value=["ph1"])
    client._graph_request = AsyncMock(return_value={"id": "999"})

    result = await client.create_photo_post("http://x/1.jpg", message="cap")
    assert result["post_id"] == "999"
    assert result["attachment_id"] == "ph1"
    client._graph_request.assert_awaited_once()


@pytest.mark.asyncio
async def test_carousel_post_attaches_media_and_counts(monkeypatch):
    client = _sock()
    client._resolved_page_id = "123"
    client._resolved_page_token = "sock"
    client._upload_photos = AsyncMock(return_value=["p1", "p2"])

    async def fake_request(method, path, *, token=None, data=None, multipart=None):
        assert data["attached_media"].startswith('[{"media_fbid":')
        assert "\"count\": 2" not in data["attached_media"]  # raw JSON, not dict
        return {"id": "555"}

    client._graph_request = fake_request

    result = await client.create_carousel_post(
        ["http://x/1.png", "http://x/2.png"], message="two"
    )
    assert result["post_id"] == "555"
    assert result["attachment_ids"] == ["p1", "p2"]
    assert result["count"] == 2


@pytest.mark.asyncio
async def test_media_post_requires_photo_ids(monkeypatch):
    client = _sock()
    client._resolved_page_id = "123"
    client._resolved_page_token = "sock"
    client._upload_photos = AsyncMock(return_value=["p1"])
    client._graph_request = AsyncMock(return_value={"id": "999"})
    result = await client.create_carousel_post(["http://x/1.png"])
    assert result["count"] == 1  # no syntax error on dict attached_media


@pytest.mark.asyncio
async def test_auth_error_maps(monkeypatch):
    client = _sock()
    client._resolved_page_id = "123"
    client._resolved_page_token = "sock"
    client._graph_request = AsyncMock(
        side_effect=AuthenticationError("token invalid")
    )
    with pytest.raises(AuthenticationError):
        await client.create_post("x")


@pytest.mark.asyncio
async def test_token_only_auth_error_contained(monkeypatch):
    client = _sock()
    client._resolved_page_id = "123"
    client._resolved_page_token = "sock"
    client._graph_request = AsyncMock(side_effect=RateLimitError("429"))
    with pytest.raises(RateLimitError):
        await client.create_post("x")


def _sock():
    return GraphApiClient("sock-token")