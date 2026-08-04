"""Tests for the Tier-1 production-gap tools.

Covers: delete_post, save_post, unsave_post, delete_message (writes) and
get_notifications, get_events, get_group_members (reads). Verifies parser
behavior on fixture HTML/JSON, PostingClient validation/limits/recording,
api_client transport honesty, confirm gates, and registry consistency.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from facebook_mcp_server import multi_account as ma
from facebook_mcp_server.core import ScrapingError
from facebook_mcp_server.posting.client import PostingClient
from facebook_mcp_server.scraping import parsers

# ─── fixtures ──────────────────────────────────────────────────────────────

NOTIF_HTML = """<script type="application/json">{"__typename":"NotifPageNotificationRow","notif":{"notif_id":"n1","seen_state":"UNSEEN_AND_UNREAD","body":{"text":"Alice commented on your post"},"notif_type":"comment","url":"https://www.facebook.com/story.php","creation_time":{"timestamp":1785772234}}}</script>
<script type="application/json">{"__typename":"NotifPageSingleFilter","title":{"text":"All"}}</script>
<script type="application/json">{"__typename":"NotifPageNotificationRow","notif":{"notif_id":"n2","seen_state":"SEEN_AND_READ","body":{"text":"Bob reacted"},"notif_type":"like","url":"","creation_time":{"timestamp":1785772235}}}</script>
"""

EVENTS_HTML = """<script type="application/json">{"events":[{"node":{"__typename":"Event","id":"e1","name":"Reading Session","start_date_time":"5 Aug at 03:30 - 05:30 IST"}},{"__typename":"Event","id":"e2","name":"Book Club","start_date_time":"6 Aug"}]}</script>
<script type="application/json">{"__typename":"Event","id":"e3"}</script>
"""

MEMBERS_JSON = {
    "payload": {
        "members": [
            {
                "__typename": "User",
                "id": "m1",
                "name": "Alice",
                "url": "https://www.facebook.com/alice",
            },
            {"id": "m2", "name": "Bob", "is_admin": True},
        ],
        "nodes": [{"__typename": "GroupMember", "id": "m3", "name": "Carol"}],
    }
}

ERROR_MEMBERS_JSON = {"__ar": 1, "error": 1357010, "errorDescription": "Something went wrong"}


@pytest.fixture
def account_store(tmp_path, monkeypatch):
    """Point the multi-account store at a temp dir with one identity."""
    root = tmp_path / "state"
    monkeypatch.setattr(ma, "auth_root_dir", lambda: root)
    ma.create_account(
        account_id="1001",
        cookies={"c_user": "1001", "xs": "secret-xs", "datr": "abc"},
        name="Test User",
        username="testuser",
        identity_type="user",
    )
    return ma


class _OkClient:
    """Fake scrape client that succeeds every Tier-1 write."""

    async def delete_post(self, post_id):
        return {"success": True, "post_id": post_id}

    async def save_post(self, post_id):
        return {"success": True, "post_id": post_id, "action": "save"}

    async def unsave_post(self, post_id):
        return {"success": True, "post_id": post_id, "action": "unsave"}

    async def delete_message(self, thread_id, message_id):
        return {"success": True, "thread_id": thread_id, "message_id": message_id}

    async def create_post(self, status):
        return {"success": True, "post_id": "p-txt-1"}


class _FailClient:
    """Fake scrape client that fails every Tier-1 write honestly."""

    async def delete_post(self, post_id):
        raise ScrapingError("decommissioned - official API required")

    async def save_post(self, post_id):
        raise ScrapingError("decommissioned - official API required")

    async def unsave_post(self, post_id):
        raise ScrapingError("decommissioned - official API required")

    async def delete_message(self, thread_id, message_id):
        raise ScrapingError("decommissioned - official API required")


@pytest.fixture
def ok_client(monkeypatch):
    def _factory(cookies, cookie_sink=None):
        del cookies, cookie_sink
        return _OkClient()

    monkeypatch.setattr("facebook_mcp_server.scraping.FacebookScrapeClient", _factory)


@pytest.fixture
def fail_client(monkeypatch):
    def _factory(cookies, cookie_sink=None):
        del cookies, cookie_sink
        return _FailClient()

    monkeypatch.setattr("facebook_mcp_server.scraping.FacebookScrapeClient", _factory)


# ─── parsers (fixture-grounded) ────────────────────────────────────────────


class TestParsers:
    def test_parse_notifications_extracts_rows_and_skips_filters(self):
        rows = parsers.parse_notifications(NOTIF_HTML, limit=10)
        assert [r["id"] for r in rows] == ["n1", "n2"]
        assert rows[0]["text"] == "Alice commented on your post"
        assert rows[0]["unread"] is True
        assert rows[0]["type"] == "comment"
        assert rows[1]["unread"] is False
        assert rows[0]["timestamp"] == 1785772234

    def test_parse_notifications_respects_limit(self):
        rows = parsers.parse_notifications(NOTIF_HTML, limit=1)
        assert len(rows) == 1
        assert rows[0]["id"] == "n1"

    def test_parse_events_extracts_named_events_skips_thin_refs(self):
        events = parsers.parse_events(EVENTS_HTML, limit=10)
        ids = [e["id"] for e in events]
        assert "e1" in ids and "e2" in ids
        assert "e3" not in ids, "thin Event refs (id only) must be skipped"
        by_id = {e["id"]: e for e in events}
        assert by_id["e1"]["name"] == "Reading Session"
        assert "5 Aug" in by_id["e1"]["start_date_time"]
        assert by_id["e1"]["url"] == "https://www.facebook.com/events/e1"

    def test_parse_group_members_handles_various_shapes(self):
        members = parsers.parse_group_members(MEMBERS_JSON, limit=10)
        assert len(members) == 3
        by_id = {m["id"]: m for m in members}
        assert by_id["m1"]["name"] == "Alice"
        assert by_id["m2"]["role"] == "admin"
        assert by_id["m3"]["name"] == "Carol"

    def test_parse_group_members_error_payload_empty(self):
        assert parsers.parse_group_members(ERROR_MEMBERS_JSON, limit=10) == []


# ─── api_client transport honesty ──────────────────────────────────────────


async def _noop() -> None:
    """Async no-op for _gate_write stubbing."""
    return None


def _stub_request(status_code: int, text: str):
    """Return an async _request replacement yielding a fake response."""

    async def _inner(method, path, **kwargs):
        del method, path, kwargs
        return SimpleNamespace(status_code=status_code, text=text)

    return _inner


def _scrape_client(monkeypatch):
    """Build a real client with dtsg stubbed (write_pace off so no gating)."""
    from facebook_mcp_server.scraping.api_client import FacebookScrapeClient

    client = FacebookScrapeClient({"c_user": "1", "xs": "x"}, write_pace=False)

    async def _dtsg() -> str:
        return "dtsg"

    monkeypatch.setattr(client, "get_fb_dtsg", _dtsg)
    return client


class TestApiClientTransports:
    @pytest.mark.asyncio
    async def test_delete_post_not_available_raises_honest(self, monkeypatch):
        client = _scrape_client(monkeypatch)
        monkeypatch.setattr(
            client, "_request", _stub_request(200, "This content isn't available anymore")
        )
        with pytest.raises(ScrapingError, match="not available"):
            await client.delete_post("123")

    @pytest.mark.asyncio
    async def test_delete_post_clean_response_is_success(self, monkeypatch):
        client = _scrape_client(monkeypatch)
        monkeypatch.setattr(client, "_request", _stub_request(200, "<html>ok</html>"))
        result = await client.delete_post("123")
        assert result["success"] is True
        assert result["post_id"] == "123"

    @pytest.mark.asyncio
    async def test_delete_post_404_surfaces_decommissioned(self, monkeypatch):
        from facebook_mcp_server.core import PageNotFoundError

        client = _scrape_client(monkeypatch)

        async def _raises_404(method, path, **kwargs):
            del method, path, kwargs
            raise PageNotFoundError("Facebook returned 404 for /delete.php")

        monkeypatch.setattr(client, "_request", _raises_404)
        with pytest.raises(ScrapingError, match="decommissioned"):
            await client.delete_post("123")

    @pytest.mark.asyncio
    async def test_delete_message_empty_body_is_success(self, monkeypatch):
        client = _scrape_client(monkeypatch)
        monkeypatch.setattr(client, "_request", _stub_request(200, ""))
        result = await client.delete_message("t1", "m1")
        assert result["success"] is True
        assert result["message_id"] == "m1"

    @pytest.mark.asyncio
    async def test_delete_message_error_body_raises(self, monkeypatch):
        client = _scrape_client(monkeypatch)
        monkeypatch.setattr(
            client,
            "_request",
            _stub_request(200, 'for (;;);{"error": {"error_msg": "nope"}}'),
        )
        with pytest.raises(ScrapingError, match="nope"):
            await client.delete_message("t1", "m1")

    @pytest.mark.asyncio
    async def test_save_post_404_decommissioned(self, monkeypatch):
        from facebook_mcp_server.core import PageNotFoundError

        client = _scrape_client(monkeypatch)

        async def _raises_404(method, path, **kwargs):
            del method, path, kwargs
            raise PageNotFoundError("Facebook returned 404 for /ajax/saved/")

        monkeypatch.setattr(client, "_request", _raises_404)
        with pytest.raises(ScrapingError, match="decommissioned"):
            await client.save_post("123")

    @pytest.mark.asyncio
    async def test_delete_post_login_wall_raises_auth(self, monkeypatch):
        """Regression: an expired session returning the login page (200 HTML)
        must raise AuthenticationError (re-login), never a false success."""
        from facebook_mcp_server.core import AuthenticationError

        client = _scrape_client(monkeypatch)
        login_html = '<html><body><form action="/login/"><input name="pass"></form></body></html>'
        monkeypatch.setattr(client, "_request", _stub_request(200, login_html))
        with pytest.raises(AuthenticationError):
            await client.delete_post("123")

    @pytest.mark.asyncio
    async def test_save_post_login_wall_raises_auth(self, monkeypatch):
        from facebook_mcp_server.core import AuthenticationError

        client = _scrape_client(monkeypatch)
        login_html = '<html><body><form action="/login/"><input name="pass"></form></body></html>'
        monkeypatch.setattr(client, "_request", _stub_request(200, login_html))
        with pytest.raises(AuthenticationError):
            await client.save_post("123")

    @pytest.mark.asyncio
    async def test_save_post_submitted_best_effort(self, monkeypatch):
        client = _scrape_client(monkeypatch)
        monkeypatch.setattr(client, "_request", _stub_request(200, "<html>ok</html>"))
        result = await client.unsave_post("123")
        assert result["success"] is True
        assert result["action"] == "unsave"

    @pytest.mark.asyncio
    async def test_get_notifications_parses_page(self, monkeypatch):
        from facebook_mcp_server.scraping.api_client import FacebookScrapeClient

        client = FacebookScrapeClient({"c_user": "1", "xs": "x"}, write_pace=False)

        async def _page(path):
            del path
            return NOTIF_HTML

        monkeypatch.setattr(client, "_get_page", _page)
        result = await client.get_notifications(limit=2)
        assert result["total"] == 2
        assert result["notifications"][0]["id"] == "n1"

    @pytest.mark.asyncio
    async def test_get_events_parses_hub(self, monkeypatch):
        from facebook_mcp_server.scraping.api_client import FacebookScrapeClient

        client = FacebookScrapeClient({"c_user": "1", "xs": "x"}, write_pace=False)

        async def _page(path):
            del path
            return EVENTS_HTML

        monkeypatch.setattr(client, "_get_page", _page)
        result = await client.get_events(limit=10)
        assert result["total"] == 2
        assert {e["id"] for e in result["events"]} == {"e1", "e2"}

    @pytest.mark.asyncio
    async def test_get_group_members_parses_ajax(self, monkeypatch):
        client = _scrape_client(monkeypatch)

        async def _ajax(*a, **k):
            del a, k
            return MEMBERS_JSON

        monkeypatch.setattr(client, "_ajax", _ajax)
        result = await client.get_group_members("g1", limit=10)
        assert result["total"] == 3

    @pytest.mark.asyncio
    async def test_get_group_members_error_is_honest(self, monkeypatch):
        client = _scrape_client(monkeypatch)

        async def _ajax(*a, **k):
            del a, k
            return ERROR_MEMBERS_JSON

        monkeypatch.setattr(client, "_ajax", _ajax)
        result = await client.get_group_members("g1", limit=10)
        assert result["members"] == []
        assert "official Graph API" in result["note"]


# ─── PostingClient: validation / limits / recording ────────────────────────


class TestPostingClient:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method", "kwargs", "match", "media_type"),
        [
            ("delete_post", {"post_id": "   "}, "post_id", "delete"),
            ("save_post", {"post_id": ""}, "post_id", "save"),
            ("unsave_post", {"post_id": ""}, "post_id", "unsave"),
            (
                "delete_message",
                {"thread_id": "", "message_id": "m1"},
                "thread_id",
                "message_delete",
            ),
            (
                "delete_message",
                {"thread_id": "t1", "message_id": ""},
                "message_id",
                "message_delete",
            ),
        ],
    )
    async def test_validation_fails_before_transport(
        self, account_store, ok_client, method, kwargs, match, media_type
    ):
        del media_type
        client = PostingClient(account_id="1001")
        with pytest.raises(ScrapingError, match=match):
            await getattr(client, method)(**kwargs)
        assert ma.get_account_posting_history("1001") == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method", "kwargs", "media_type"),
        [
            ("delete_post", {"post_id": "123"}, "delete"),
            ("save_post", {"post_id": "123"}, "save"),
            ("unsave_post", {"post_id": "123"}, "unsave"),
            ("delete_message", {"thread_id": "t1", "message_id": "m1"}, "message_delete"),
        ],
    )
    async def test_success_records_attempt(
        self, account_store, ok_client, method, kwargs, media_type
    ):
        client = PostingClient(account_id="1001")
        result = await getattr(client, method)(**kwargs)
        assert result.get("success") is True
        history = ma.get_account_posting_history("1001")
        assert history, f"{method} should record an attempt"
        assert history[-1]["media_type"] == media_type
        assert history[-1]["success"] is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method", "kwargs", "media_type"),
        [
            ("delete_post", {"post_id": "123"}, "delete"),
            ("save_post", {"post_id": "123"}, "save"),
            ("unsave_post", {"post_id": "123"}, "unsave"),
            ("delete_message", {"thread_id": "t1", "message_id": "m1"}, "message_delete"),
        ],
    )
    async def test_transport_failure_recorded_and_surfaces(
        self, account_store, fail_client, method, kwargs, media_type
    ):
        client = PostingClient(account_id="1001")
        with pytest.raises(ScrapingError, match="decommissioned"):
            await getattr(client, method)(**kwargs)
        history = ma.get_account_posting_history("1001")
        assert history, f"{method} should record the failed attempt"
        assert history[-1]["media_type"] == media_type
        assert history[-1]["success"] is False
        assert "decommissioned" in (history[-1]["error_message"] or "")

    @pytest.mark.asyncio
    async def test_posting_limits_block_delete(self, account_store, ok_client):
        acc = ma.get_account("1001")
        acc.daily_post_limit = 2
        acc.last_post_time = "2020-01-01T00:00:00+00:00"
        ma._write_account_metadata(ma.account_metadata_path("1001"), acc)
        ma.record_post_attempt("1001", "text", True, post_id="p1")
        ma.record_post_attempt("1001", "text", True, post_id="p2")

        client = PostingClient(account_id="1001")
        with pytest.raises(ScrapingError, match="limit"):
            await client.delete_post("123")


# ─── confirm gates + registry (all 4 new writes) ───────────────────────────


class TestSurface:
    NEW_TOOLS = {
        "delete_post": True,
        "save_post": True,
        "unsave_post": True,
        "delete_message": True,
        "get_notifications": False,
        "get_events": False,
        "get_group_members": False,
    }

    async def test_new_tools_registered_with_correct_annotations(self):
        from facebook_mcp_server.server import create_mcp_server

        mcp = create_mcp_server()
        tools = {t.name: t for t in await mcp.list_tools()}
        for name, is_write in self.NEW_TOOLS.items():
            assert name in tools, f"Missing new tool: {name}"
            tool = tools[name]
            props = (tool.parameters or {}).get("properties", {})
            if is_write:
                assert tool.annotations.writeHint, f"{name} missing writeHint"
                assert "confirm" in props, f"{name} missing confirm gate"
                assert "account_id" in props, f"{name} missing account_id override"
            else:
                assert tool.annotations.readOnlyHint, f"{name} missing readOnlyHint"

    async def test_new_tools_in_registry(self):
        from facebook_mcp_server.tool_registry import TOOLS

        names = {name for name, _ in TOOLS}
        for name in self.NEW_TOOLS:
            assert name in names, f"{name} missing from CLI registry"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("tool", "kwargs"),
        [
            ("delete_post", {"post_id": "123"}),
            ("save_post", {"post_id": "123"}),
            ("unsave_post", {"post_id": "123"}),
            ("delete_message", {"thread_id": "t1", "message_id": "m1"}),
        ],
    )
    async def test_confirm_gate_blocks_without_confirm(self, tool, kwargs):
        from facebook_mcp_server.server import create_mcp_server

        mcp = create_mcp_server()
        t = {x.name: x for x in await mcp.list_tools()}[tool]
        with pytest.raises(Exception) as excinfo:
            await t.fn(**kwargs, confirm=False)
        assert "confirm=true" in str(excinfo.value)
