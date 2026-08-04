"""Tests for the MCP tool layer using a mocked ready client."""

import json

import pytest

from facebook_mcp_server.scraping import FacebookScrapeClient
from tests.conftest import FakeResponse, is_home_url

HOME_HTML = '<script type="application/json">{"dtsg":{"token":"TOKEN_123"}}</script>'
PROFILE_HTML = (
    '<script type="application/json">'
    + json.dumps(
        {
            "user": {
                "userID": "1",
                "name": "Page One",
                "profileURL": "https://www.facebook.com/page1",
            }
        }
    )
    + "</script>"
)
POSTS_HTML = (
    '<script type="application/json">'
    + json.dumps(
        {
            "feed": [
                {
                    "comet_sections": {},
                    "id": "111",
                    "message": {"text": "Hello"},
                    "creation_time": 1700000000,
                }
            ]
        }
    )
    + "</script>"
)


@pytest.fixture
def ready_client(monkeypatch, fake_session):
    """Patch get_ready_client to return a client backed by FakeSession."""

    async def _get_ready_client(ctx, *, tool_name):
        del ctx, tool_name
        return FacebookScrapeClient({"c_user": "1", "xs": "s"})

    from facebook_mcp_server.tools import (
        account_tools,
        comment_tools,
        group_tools,
        messenger_tools,
        page_tools,
        posting_tools,
        post_tools,
    )  # Modules that use get_ready_client for read tools.

    for mod in (
        account_tools,
        comment_tools,
        group_tools,
        messenger_tools,
        page_tools,
        post_tools,
    ):
        monkeypatch.setattr(mod, "get_ready_client", _get_ready_client)

    # Write modules import get_ready_posting_client.
    async def _get_ready_posting_client(ctx, *, tool_name, account_id=None):
        del ctx, tool_name, account_id
        return FacebookScrapeClient({"c_user": "1", "xs": "s"})

    for mod in (comment_tools, messenger_tools, posting_tools):
        monkeypatch.setattr(mod, "get_ready_posting_client", _get_ready_posting_client)

    return fake_session


async def _call(mcp, tool_name, **kwargs):
    """Invoke a registered tool via its fn (mirrors the CLI direct path)."""
    tools = await mcp.list_tools()
    tool = next(t for t in tools if t.name == tool_name)
    return await tool.fn(**kwargs)


class TestAccountTools:
    @pytest.mark.asyncio
    async def test_get_me(self, ready_client):
        def handler(method, url, params, data, files):
            return FakeResponse(status_code=200, text=HOME_HTML, url="https://www.facebook.com/")

        ready_client.routes = [("GET", "facebook.com/", handler)]
        from facebook_mcp_server.server import create_mcp_server

        result = await _call(create_mcp_server(), "get_me")
        assert result["me"]["id"] == "1"

    @pytest.mark.asyncio
    async def test_set_active_page(self, ready_client, tmp_path):
        from facebook_mcp_server.server import create_mcp_server

        result = await _call(
            create_mcp_server(), "set_active_page", page_id="999", page_name="My Page"
        )
        assert result["status"] == "ok"
        assert result["active_page_id"] == "999"


class TestPageTools:
    @pytest.mark.asyncio
    async def test_get_page_info(self, ready_client):
        def handler(method, url, params, data, files):
            return FakeResponse(
                status_code=200, text=PROFILE_HTML, url="https://www.facebook.com/page1/"
            )

        ready_client.routes = [("GET", "facebook.com/page1", handler)]
        from facebook_mcp_server.server import create_mcp_server

        result = await _call(create_mcp_server(), "get_page_info", username="page1")
        assert result["name"] == "Page One"

    @pytest.mark.asyncio
    async def test_get_page_posts(self, ready_client):
        def handler(method, url, params, data, files):
            return FakeResponse(
                status_code=200, text=POSTS_HTML, url="https://www.facebook.com/page1/"
            )

        ready_client.routes = [("GET", "facebook.com/page1", handler)]
        from facebook_mcp_server.server import create_mcp_server

        result = await _call(create_mcp_server(), "get_page_posts", username="page1", max_posts=5)
        assert result["total"] == 1
        assert result["posts"][0]["text"] == "Hello"


class TestWriteGuards:
    @pytest.mark.asyncio
    async def test_create_post_requires_confirm(self, ready_client):
        from facebook_mcp_server.server import create_mcp_server

        with pytest.raises(Exception) as excinfo:
            await _call(create_mcp_server(), "create_post", status="hello", confirm=False)
        assert "confirm=true" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_create_video_post_requires_confirm(self, ready_client):
        from facebook_mcp_server.server import create_mcp_server

        with pytest.raises(Exception) as excinfo:
            await _call(
                create_mcp_server(),
                "create_video_post",
                source="/tmp/v.mp4",
                confirm=False,
            )
        assert "confirm=true" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_create_carousel_post_requires_confirm(self, ready_client):
        from facebook_mcp_server.server import create_mcp_server

        with pytest.raises(Exception) as excinfo:
            await _call(
                create_mcp_server(),
                "create_carousel_post",
                sources=["/tmp/a.jpg", "/tmp/b.jpg"],
                confirm=False,
            )
        assert "confirm=true" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_send_message_requires_confirm(self, ready_client):
        from facebook_mcp_server.server import create_mcp_server

        with pytest.raises(Exception) as excinfo:
            await _call(
                create_mcp_server(),
                "send_message",
                thread_id="t1",
                message="hi",
                confirm=False,
            )
        assert "confirm=true" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_comment_requires_confirm(self, ready_client):
        from facebook_mcp_server.server import create_mcp_server

        with pytest.raises(Exception) as excinfo:
            await _call(
                create_mcp_server(),
                "comment_on_post",
                post_id="p1",
                text="nice",
                confirm=False,
            )
        assert "confirm=true" in str(excinfo.value)


class TestMessengerTools:
    @pytest.mark.asyncio
    async def test_get_conversations(self, ready_client):
        def dtsg_handler(method, url, params, data, files):
            return FakeResponse(status_code=200, text=HOME_HTML, url="https://www.facebook.com/")

        def threadlist_handler(method, url, params, data, files):
            return FakeResponse(
                status_code=200,
                json_data={"threads": [{"thread_fbid": "t1", "name": "Alice", "snippet": "hi"}]},
            )

        ready_client.routes = [
            ("GET", is_home_url, dtsg_handler),
            ("GET", "threadlist_info.php", threadlist_handler),
        ]
        from facebook_mcp_server.server import create_mcp_server

        result = await _call(create_mcp_server(), "get_conversations", limit=5)
        assert result["conversations"][0]["thread_id"] == "t1"


class TestErrorSurfacing:
    @pytest.mark.asyncio
    async def test_client_error_surfaces_as_tool_error(self, ready_client):
        from fastmcp.exceptions import ToolError

        def handler(method, url, params, data, files):
            return FakeResponse(status_code=404, text="not found")

        ready_client.routes = [("GET", "facebook.com/gone", handler)]
        from facebook_mcp_server.server import create_mcp_server

        with pytest.raises(ToolError):
            await _call(create_mcp_server(), "get_page_info", username="gone")
