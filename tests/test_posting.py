"""Tests for the facebook posting package (validators, media, client)."""

from __future__ import annotations

from pathlib import Path

import pytest

from facebook_mcp_server.posting.client import PostingClient
from facebook_mcp_server.posting.media_processor import MediaProcessor
from facebook_mcp_server.posting.validators import PostingValidator


# ─── validators ────────────────────────────────────────────────────────────


class TestPostingValidator:
    def test_validate_status_ok(self):
        valid, msg = PostingValidator.validate_status("hello world")
        assert valid
        assert msg == ""

    def test_validate_status_empty(self):
        valid, msg = PostingValidator.validate_status("   ")
        assert not valid
        assert "cannot be empty" in msg

    def test_validate_status_too_long(self):
        valid, msg = PostingValidator.validate_status("x" * 70000)
        assert not valid
        assert "too long" in msg

    def test_validate_caption_empty_ok(self):
        valid, msg = PostingValidator.validate_caption("")
        assert valid

    def test_validate_photo_path_missing(self):
        valid, msg = PostingValidator.validate_photo_path("/nonexistent/x.jpg")
        assert not valid
        assert "not found" in msg

    def test_validate_photo_path_bad_ext(self, tmp_path):
        f = tmp_path / "note.txt"
        f.write_text("hi")
        valid, msg = PostingValidator.validate_photo_path(str(f))
        assert not valid
        assert "Invalid image format" in msg

    def test_validate_video_path_ok(self, tmp_path):
        f = tmp_path / "clip.mp4"
        f.write_bytes(b"\x00\x00")
        valid, msg = PostingValidator.validate_video_path(str(f))
        assert valid
        assert msg == ""

    def test_validate_carousel_too_few(self):
        valid, msg = PostingValidator.validate_carousel_paths(["/tmp/a.jpg"])
        assert not valid
        assert "at least" in msg

    def test_validate_media_source_url_ok(self):
        valid, msg = PostingValidator.validate_media_source("https://example.com/a.jpg")
        assert valid

    def test_validate_aspect_ratio(self):
        valid, _ = PostingValidator.validate_aspect_ratio(1200, 630)
        assert valid
        valid, msg = PostingValidator.validate_aspect_ratio(5000, 100)
        assert not valid
        assert "aspect ratio" in msg


# ─── media processor ───────────────────────────────────────────────────────


class TestMediaProcessor:
    def test_process_image_smart(self, tmp_path):
        pytest.importorskip("PIL")
        from PIL import Image

        src = tmp_path / "big.png"
        Image.new("RGB", (4000, 2000), (200, 30, 40)).save(src)
        out = MediaProcessor.process_image_smart(str(src))
        assert Path(out).exists()
        img = Image.open(out)
        assert max(img.size) <= MediaProcessor.MAX_IMAGE_SIZE
        Path(out).unlink(missing_ok=True)

    def test_validate_image_ok(self, tmp_path):
        pytest.importorskip("PIL")
        from PIL import Image

        src = tmp_path / "ok.jpg"
        Image.new("RGB", (10, 10)).save(src, "JPEG")
        assert MediaProcessor.validate_image(str(src))

    def test_validate_image_bad(self, tmp_path):
        src = tmp_path / "bad.jpg"
        src.write_bytes(b"not an image")
        assert not MediaProcessor.validate_image(str(src))

    def test_video_metadata_missing_file(self, tmp_path):
        meta = MediaProcessor.video_metadata(str(tmp_path / "nope.mp4"))
        assert isinstance(meta, dict)


# ─── posting client ────────────────────────────────────────────────────────


@pytest.fixture
def account_store(tmp_path, monkeypatch):
    """Point the multi-account store at a temp dir and create an identity."""
    from facebook_mcp_server import multi_account as ma

    root = tmp_path / "state"
    monkeypatch.setattr(ma, "_ACCOUNTS_DIR", "accounts")
    monkeypatch.setattr(ma, "auth_root_dir", lambda: root)

    cookies = {"c_user": "1001", "xs": "secret-xs", "datr": "abc"}
    ma.create_account(
        account_id="1001",
        cookies=cookies,
        name="Test User",
        username="testuser",
        identity_type="user",
    )
    return ma


class TestPostingClient:
    async def test_resolves_account_cookies(self, account_store, monkeypatch):
        captured: dict = {}

        class _FakeScrape:
            async def create_post(self, status):  # noqa: ARG002
                return {"success": True, "post_id": "p123"}

        def _fake_factory(cookies, cookie_sink=None):
            captured["cookies"] = cookies
            captured["sink"] = cookie_sink
            return _FakeScrape()

        monkeypatch.setattr("facebook_mcp_server.scraping.FacebookScrapeClient", _fake_factory)
        client = PostingClient(account_id="1001")
        assert client._resolved_account_id == "1001"
        assert captured["cookies"]["c_user"] == "1001"
        assert captured["sink"] is not None, "cookie sink should be wired for accounts"

        result = await client.create_text_post("hello")
        assert result["post_id"] == "p123"

    async def test_invalid_status_raises(self, account_store):
        client = PostingClient(account_id="1001")
        from facebook_mcp_server.core import ScrapingError

        with pytest.raises(ScrapingError, match="cannot be empty"):
            await client.create_text_post("   ")

    async def test_records_attempt_on_failure(self, account_store, monkeypatch):
        from facebook_mcp_server.core import ScrapingError

        class _Failing:
            async def create_post(self, status):  # noqa: ARG002
                raise ScrapingError("decommissioned — official API required")

        monkeypatch.setattr(
            "facebook_mcp_server.scraping.FacebookScrapeClient",
            lambda c, cookie_sink=None: _Failing(),
        )
        client = PostingClient(account_id="1001")
        with pytest.raises(ScrapingError, match="decommissioned"):
            await client.create_text_post("hello")

        history = account_store.get_account_posting_history("1001")
        assert history, "attempt should be recorded"
        assert history[-1]["success"] is False
        assert history[-1]["media_type"] == "text"

    async def test_missing_account_raises(self, tmp_path, monkeypatch):
        """A missing stored account is a config error (ScrapingError), NOT an
        AuthenticationError — it must never trigger the re-login flow."""
        from facebook_mcp_server import multi_account as ma

        root = tmp_path / "state2"
        monkeypatch.setattr(ma, "auth_root_dir", lambda: root)
        from facebook_mcp_server.core import AuthenticationError, ScrapingError

        with pytest.raises(ScrapingError, match="not found"):
            PostingClient(account_id="9999")
        # Guard: ensure it is NOT an AuthenticationError
        try:
            PostingClient(account_id="9999")
        except ScrapingError as exc:
            assert not isinstance(exc, AuthenticationError)
