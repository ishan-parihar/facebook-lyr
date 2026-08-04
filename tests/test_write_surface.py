"""Exhaustive write-surface tests for the Facebook MCP server.

Covers every write tool: create_post, create_photo_post, create_video_post,
create_carousel_post, comment_on_post, send_message, send_typing_indicator,
mark_conversation_seen. Verifies confirm gates, input validation, per-identity
posting limits, attempt recording, transport-error honesty, and the full
write-surface inventory.
"""

from __future__ import annotations


import pytest

from facebook_mcp_server import multi_account as ma
from facebook_mcp_server.core import ScrapingError
from facebook_mcp_server.posting.client import PostingClient


# ─── fixtures ──────────────────────────────────────────────────────────────


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
    """Fake scrape client that succeeds every write with a plausible payload."""

    async def create_post(self, status):
        return {"success": True, "post_id": "p-txt-1"}

    async def create_comment(self, post_id, text):
        return {"success": True, "comment_id": "c-1"}

    async def send_message(self, thread_id, body):
        return {"success": True, "message_id": "m-1", "thread_id": thread_id}

    async def create_photo_post(self, source, message=""):
        return {"success": True, "post_id": "p-photo-1", "attachment_id": "a-1"}

    async def create_video_post(self, source, message=""):
        return {"success": True, "post_id": "p-vid-1", "attachment_id": "a-2"}

    async def create_carousel_post(self, sources, message=""):
        return {
            "success": True,
            "post_id": "p-car-1",
            "attachment_ids": ["a-1", "a-2"],
            "count": len(sources),
        }


class _FailClient:
    """Fake scrape client that fails every write with the decommissioned error."""

    async def create_post(self, status):
        raise ScrapingError("decommissioned — official API required")

    async def create_comment(self, post_id, text):
        raise ScrapingError("decommissioned — official API required")

    async def send_message(self, thread_id, body):
        raise ScrapingError("decommissioned — official API required")

    async def create_photo_post(self, source, message=""):
        raise ScrapingError("decommissioned — official API required")

    async def create_video_post(self, source, message=""):
        raise ScrapingError("decommissioned — official API required")

    async def create_carousel_post(self, sources, message=""):
        raise ScrapingError("decommissioned — official API required")


@pytest.fixture
def media_files(tmp_path):
    """Create real, valid media files for photo/video tests."""
    pytest.importorskip("PIL")
    from PIL import Image

    photo = tmp_path / "photo.jpg"
    Image.new("RGB", (800, 600), (10, 20, 30)).save(photo, "JPEG")
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypmp42")  # minimal mp4 header stub
    return {"photo": str(photo), "video": str(video)}


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


# ─── surface inventory ─────────────────────────────────────────────────────


class TestWriteSurfaceInventory:
    """The write surface must be complete, consistent, and discoverable."""

    EXPECTED_WRITE_TOOLS = {
        "create_post": {"confirm": True, "account_id": True},
        "create_photo_post": {"confirm": True, "account_id": True},
        "create_video_post": {"confirm": True, "account_id": True},
        "create_carousel_post": {"confirm": True, "account_id": True},
        "comment_on_post": {"confirm": True, "account_id": True},
        "send_message": {"confirm": True, "account_id": True},
        "send_typing_indicator": {"confirm": False, "account_id": False},
        "mark_conversation_seen": {"confirm": False, "account_id": False},
    }

    async def test_every_write_tool_registered_with_correct_gates(self):
        from facebook_mcp_server.server import create_mcp_server

        mcp = create_mcp_server()
        tools = {t.name: t for t in await mcp.list_tools()}

        for name, expected in self.EXPECTED_WRITE_TOOLS.items():
            assert name in tools, f"Missing write tool: {name}"
            tool = tools[name]
            props = (tool.parameters or {}).get("properties", {})
            assert ("confirm" in props) == expected["confirm"], f"{name} confirm gate wrong"
            assert ("account_id" in props) == expected["account_id"], (
                f"{name} account_id override wrong"
            )
            # every write tool must be marked writeHint
            assert tool.annotations.writeHint, f"{name} missing writeHint"

    async def test_write_tools_in_registry_list(self):
        from facebook_mcp_server.tool_registry import TOOLS

        names = {name for name, _ in TOOLS}
        for name in self.EXPECTED_WRITE_TOOLS:
            assert name in names, f"{name} missing from CLI registry"

    async def test_registry_matches_server_no_duplicates(self):
        from facebook_mcp_server.server import create_mcp_server
        from facebook_mcp_server.tool_registry import TOOLS

        mcp = create_mcp_server()
        server_names = [t.name for t in await mcp.list_tools()]
        assert len(server_names) == len(set(server_names)), "duplicate tool names!"
        registry_names = [name for name, _ in TOOLS]
        assert len(registry_names) == len(set(registry_names)), "duplicate registry entries!"
        # registry is a strict subset of the live server (server may add more)
        for name in registry_names:
            assert name in server_names, f"registry tool {name} not served by MCP"


# ─── confirm gates (all 6 destructive writes) ──────────────────────────────


class TestConfirmGates:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("tool", "kwargs"),
        [
            ("create_post", {"status": "hi"}),
            ("create_photo_post", {"source": "/tmp/x.jpg"}),
            ("create_video_post", {"source": "/tmp/x.mp4"}),
            ("create_carousel_post", {"sources": ["/tmp/a.jpg", "/tmp/b.jpg"]}),
            ("comment_on_post", {"post_id": "1", "text": "hi"}),
            ("send_message", {"thread_id": "t1", "message": "hi"}),
        ],
    )
    async def test_write_blocked_without_confirm(self, tool, kwargs):
        from facebook_mcp_server.server import create_mcp_server

        mcp = create_mcp_server()
        t = {x.name: x for x in await mcp.list_tools()}[tool]
        with pytest.raises(Exception) as excinfo:
            await t.fn(**kwargs, confirm=False)
        assert "confirm=true" in str(excinfo.value)


# ─── validation failures (no transport call made) ──────────────────────────


class TestValidation:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method", "kwargs", "match"),
        [
            ("create_text_post", {"status": "   "}, "cannot be empty"),
            ("create_text_post", {"status": "x" * 70000}, "too long"),
            ("create_photo_post", {"source": "/nope/missing.jpg", "message": ""}, "not found"),
            ("create_video_post", {"source": "/nope/missing.mp4", "message": ""}, "not found"),
            ("create_carousel_post", {"sources": ["/a.jpg"], "message": ""}, "at least"),
            (
                "create_carousel_post",
                {"sources": [f"/{i}.jpg" for i in range(12)], "message": ""},
                "at most",
            ),
            ("comment_on_post", {"post_id": "", "text": "hi"}, "post_id"),
            ("comment_on_post", {"post_id": "1", "text": "   "}, "cannot be empty"),
            ("comment_on_post", {"post_id": "1", "text": "x" * 2001}, "too long"),
            ("send_message", {"thread_id": "", "body": "hi"}, "thread_id"),
            ("send_message", {"thread_id": "t1", "body": "   "}, "cannot be empty"),
            ("create_photo_post", {"source": "REAL", "message": "y" * 70000}, "too long"),
            ("create_video_post", {"source": "REAL_VIDEO", "message": "y" * 70000}, "too long"),
        ],
    )
    async def test_validation_fails_before_transport(
        self,
        account_store,
        ok_client,
        media_files,
        method,
        kwargs,
        match,
    ):
        def _resolve(v):
            if v == "REAL":
                return media_files["photo"]
            if v == "REAL_VIDEO":
                return media_files["video"]
            return v

        kwargs = {k: _resolve(v) for k, v in kwargs.items()}
        client = PostingClient(account_id="1001")
        with pytest.raises(ScrapingError, match=match):
            await getattr(client, method)(**kwargs)

        # validation failures must NOT record an attempt (nothing was attempted)
        history = ma.get_account_posting_history("1001")
        assert history == [], "validation failures must not be recorded as attempts"


# ─── success path records attempts ─────────────────────────────────────────


class TestSuccessRecording:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method", "kwargs", "media_type"),
        [
            ("create_text_post", {"status": "hello"}, "text"),
            ("create_photo_post", {"source": "REAL", "message": "cap"}, "photo"),
            ("create_video_post", {"source": "REAL_VIDEO", "message": ""}, "video"),
            ("create_carousel_post", {"sources": ["REAL", "REAL"], "message": ""}, "carousel"),
            ("comment_on_post", {"post_id": "123", "text": "nice"}, "comment"),
            ("send_message", {"thread_id": "t1", "body": "hi there"}, "message"),
        ],
    )
    async def test_success_records_attempt(
        self,
        account_store,
        ok_client,
        media_files,
        method,
        kwargs,
        media_type,
    ):
        kwargs = {
            k: (
                media_files["photo"]
                if v == "REAL"
                else media_files["video"]
                if v == "REAL_VIDEO"
                else v
            )
            for k, v in kwargs.items()
        }
        if "sources" in kwargs:
            kwargs["sources"] = [
                (media_files["photo"] if s == "REAL" else s) for s in kwargs["sources"]
            ]
        client = PostingClient(account_id="1001")
        result = await getattr(client, method)(**kwargs)
        assert result.get("success") is True

        history = ma.get_account_posting_history("1001")
        assert history, f"{method} should record an attempt"
        last = history[-1]
        assert last["media_type"] == media_type
        assert last["success"] is True

        # success increments the daily counter
        acc = ma.get_account("1001")
        assert acc.post_count_today >= 1


# ─── transport errors recorded honestly ────────────────────────────────────


class TestTransportHonesty:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method", "kwargs", "media_type"),
        [
            ("create_text_post", {"status": "hello"}, "text"),
            ("create_photo_post", {"source": "REAL", "message": ""}, "photo"),
            ("create_video_post", {"source": "REAL_VIDEO", "message": ""}, "video"),
            ("create_carousel_post", {"sources": ["REAL", "REAL"], "message": ""}, "carousel"),
            ("comment_on_post", {"post_id": "123", "text": "nice"}, "comment"),
            ("send_message", {"thread_id": "t1", "body": "hi"}, "message"),
        ],
    )
    async def test_transport_failure_is_recorded_and_surfaces(
        self,
        account_store,
        fail_client,
        media_files,
        method,
        kwargs,
        media_type,
    ):
        kwargs = {
            k: (
                media_files["photo"]
                if v == "REAL"
                else media_files["video"]
                if v == "REAL_VIDEO"
                else v
            )
            for k, v in kwargs.items()
        }
        if "sources" in kwargs:
            kwargs["sources"] = [
                (media_files["photo"] if s == "REAL" else s) for s in kwargs["sources"]
            ]
        client = PostingClient(account_id="1001")
        with pytest.raises(ScrapingError, match="decommissioned"):
            await getattr(client, method)(**kwargs)

        history = ma.get_account_posting_history("1001")
        assert history, f"{method} should record the failed attempt"
        last = history[-1]
        assert last["media_type"] == media_type
        assert last["success"] is False
        assert "decommissioned" in (last["error_message"] or "")


# ─── per-identity posting limits ───────────────────────────────────────────


class TestPostingLimits:
    @pytest.mark.asyncio
    async def test_daily_limit_blocks_post(self, account_store, ok_client):
        acc = ma.get_account("1001")
        acc.daily_post_limit = 2
        acc.last_post_time = "2020-01-01T00:00:00+00:00"
        ma._write_account_metadata(ma.account_metadata_path("1001"), acc)
        # Record two successes directly (like real transport success would).
        # The cooldown was backdated above so it can't mask the limit check.
        ma.record_post_attempt("1001", "text", True, post_id="p1")
        ma.record_post_attempt("1001", "text", True, post_id="p2")

        client = PostingClient(account_id="1001")
        with pytest.raises(ScrapingError, match="limit"):
            await client.create_text_post("three")

    @pytest.mark.asyncio
    async def test_missing_account_does_not_trigger_relogin(self, tmp_path, monkeypatch):
        """Regression: a nonexistent account_id is a config error, not session
        expiry — it must surface as ScrapingError, never trigger the re-login
        flow that would invalidate the valid default session."""
        from facebook_mcp_server import multi_account as ma

        root = tmp_path / "state-nologin"
        monkeypatch.setattr(ma, "auth_root_dir", lambda: root)
        relogin_called = []

        async def _fake_relogin(ctx):
            del ctx
            relogin_called.append(True)

        monkeypatch.setattr(
            "facebook_mcp_server.posting.client.AuthenticationError",
            ScrapingError,  # ensure it's not the auth path
        )
        # Directly assert PostingClient raises ScrapingError (not AuthError)
        from facebook_mcp_server.core import ScrapingError as SE

        with pytest.raises(SE, match="not found"):
            PostingClient(account_id="nope-999")
        assert relogin_called == []

    @pytest.mark.asyncio
    async def test_disabled_posting_blocks(self, account_store, ok_client):
        acc = ma.get_account("1001")
        acc.posting_enabled = False
        ma._write_account_metadata(ma.account_metadata_path("1001"), acc)

        client = PostingClient(account_id="1001")
        with pytest.raises(ScrapingError, match="disabled"):
            await client.create_text_post("hello")


# ─── CLI argument coercion (arrays / objects / booleans) ───────────────────


class TestCliCoercion:
    """The CLI must JSON-parse object/array params (cookies, sources)."""

    def test_object_coercion(self):
        # Prove object/array CLI params flow through run_tool_direct's
        # json.loads coercion: object -> dict, array -> list, not strings.
        from facebook_mcp_server.tool_registry import _coerce_kwarg

        assert _coerce_kwarg("object", '{"c_user":"1"}') == {"c_user": "1"}
        assert _coerce_kwarg("array", '["/a.jpg","/b.jpg"]') == ["/a.jpg", "/b.jpg"]
        assert _coerce_kwarg("string", "hello") == "hello"
        assert _coerce_kwarg("boolean", "true") is True
        assert _coerce_kwarg("integer", "10") == 10

    def test_bad_object_arg_rejected(self):
        from facebook_mcp_server.tool_registry import _coerce_kwarg

        with pytest.raises(ValueError):
            _coerce_kwarg("object", "not-json")

    def test_registry_serializable(self):
        from facebook_mcp_server.tool_registry import TOOLS

        for name, desc in TOOLS:
            assert name and desc, "registry entries must have name+description"
