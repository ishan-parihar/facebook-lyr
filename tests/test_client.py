"""Tests for the cookie-based FacebookScrapeClient."""

import json

import pytest

from facebook_mcp_server.core import (
    AuthenticationError,
    NetworkError,
    RateLimitError,
    ScrapingError,
)
from facebook_mcp_server.scraping import FacebookScrapeClient, parsers
from tests.conftest import FakeResponse, is_home_url

HOME_HTML = '<script type="application/json">{"dtsg":{"token":"TOKEN_123"}}</script><div>home</div>'
PROFILE_HTML = (
    '<script type="application/json">'
    + json.dumps(
        {
            "user": {
                "userID": "1000001",
                "name": "Test Person",
                "shortName": "test.person",
                "profileURL": "https://www.facebook.com/test.person",
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
                    "message": {"text": "A post"},
                    "creation_time": 1700000000,
                }
            ]
        }
    )
    + "</script>"
)


def make_client() -> FacebookScrapeClient:
    return FacebookScrapeClient({"c_user": "1000001", "xs": "s3cret"})


class TestRequestHandling:
    @pytest.mark.asyncio
    async def test_login_wall_raises_auth_error(self, fake_session):
        def handler(method, url, params, data, files):
            return FakeResponse(
                status_code=200,
                text='<html>"Log in to continue"</html>',
                url="https://www.facebook.com/login/",
            )

        fake_session.routes = [("GET", "facebook.com/", handler)]
        client = make_client()
        with pytest.raises(AuthenticationError):
            await client.get_fb_dtsg()

    @pytest.mark.asyncio
    async def test_429_retries_then_raises(self, fake_session, monkeypatch):
        async def never_sleep(secs):
            return None

        monkeypatch.setattr(FacebookScrapeClient, "_sleep", staticmethod(never_sleep))

        def handler(method, url, params, data, files):
            return FakeResponse(status_code=429, headers={"Retry-After": "1"})

        fake_session.routes = [("GET", "facebook.com/", handler)]
        client = make_client()
        with pytest.raises(RateLimitError):
            await client.get_fb_dtsg()

    @pytest.mark.asyncio
    async def test_network_error(self, fake_session):
        from curl_cffi.requests import exceptions as cffi_exc

        class Boom(cffi_exc.ConnectionError):
            pass

        async def explode(method, url, params=None, data=None, files=None, **kw):
            raise Boom("connection refused")

        fake_session.routes = [("GET", "facebook.com/", explode)]
        client = make_client()
        with pytest.raises(NetworkError):
            await client.get_fb_dtsg()

    @pytest.mark.asyncio
    async def test_non_json_ajax_raises(self, fake_session):
        def dtsg_handler(method, url, params, data, files):
            return FakeResponse(status_code=200, text=HOME_HTML, url="https://www.facebook.com/")

        def handler(method, url, params, data, files):
            return FakeResponse(status_code=200, text="<html>not json</html>")

        fake_session.routes = [
            ("GET", is_home_url, dtsg_handler),
            ("GET", "threadlist_info.php", handler),
        ]
        client = make_client()
        with pytest.raises(ScrapingError):
            await client.get_thread_list()


class TestSession:
    @pytest.mark.asyncio
    async def test_get_fb_dtsg_cached(self, fake_session):
        def handler(method, url, params, data, files):
            return FakeResponse(status_code=200, text=HOME_HTML, url="https://www.facebook.com/")

        fake_session.routes = [("GET", "facebook.com/", handler)]
        client = make_client()
        assert await client.get_fb_dtsg() == "TOKEN_123"
        assert await client.get_fb_dtsg() == "TOKEN_123"
        # cached — no second request
        assert len(fake_session.instances[0].calls) == 1

    @pytest.mark.asyncio
    async def test_validate_session_valid(self, fake_session):
        def handler(method, url, params, data, files):
            return FakeResponse(status_code=200, text=HOME_HTML, url="https://www.facebook.com/")

        fake_session.routes = [("GET", "facebook.com/", handler)]
        assert await make_client().validate_session() is True

    @pytest.mark.asyncio
    async def test_validate_session_invalid(self, fake_session):
        def handler(method, url, params, data, files):
            return FakeResponse(
                status_code=200,
                text='<html>"Log in to continue"</html>',
                url="https://www.facebook.com/login/",
            )

        fake_session.routes = [("GET", "facebook.com/", handler)]
        assert await make_client().validate_session() is False

    @pytest.mark.asyncio
    async def test_get_me_from_cookies(self, fake_session):
        def handler(method, url, params, data, files):
            return FakeResponse(status_code=200, text=HOME_HTML, url="https://www.facebook.com/")

        fake_session.routes = [("GET", "facebook.com/", handler)]
        me = await make_client().get_me()
        assert me["id"] == "1000001"


class TestProfiles:
    @pytest.mark.asyncio
    async def test_get_profile(self, fake_session):
        def handler(method, url, params, data, files):
            return FakeResponse(
                status_code=200, text=PROFILE_HTML, url="https://www.facebook.com/test.person/"
            )

        fake_session.routes = [("GET", "facebook.com/test.person", handler)]
        profile = await make_client().get_profile("test.person")
        assert profile["id"] == "1000001"
        assert profile["name"] == "Test Person"

    @pytest.mark.asyncio
    async def test_get_profile_posts(self, fake_session):
        def handler(method, url, params, data, files):
            return FakeResponse(
                status_code=200, text=POSTS_HTML, url="https://www.facebook.com/test.person/"
            )

        fake_session.routes = [("GET", "facebook.com/test.person", handler)]
        result = await make_client().get_profile_posts("test.person", limit=5)
        assert result["total"] == 1
        assert result["posts"][0]["text"] == "A post"


class TestMessenger:
    THREADLIST = json.dumps({"threads": [{"thread_fbid": "t1", "name": "Alice", "snippet": "hi"}]})
    THREAD = json.dumps(
        {
            "thread": {
                "messages": [
                    {
                        "message_id": "m1",
                        "message_sender": {"name": "Alice"},
                        "message": {"text": "hey"},
                    }
                ]
            }
        }
    )

    @pytest.mark.asyncio
    async def test_get_thread_list(self, fake_session):
        def dtsg_handler(method, url, params, data, files):
            return FakeResponse(status_code=200, text=HOME_HTML, url="https://www.facebook.com/")

        def threadlist_handler(method, url, params, data, files):
            return FakeResponse(status_code=200, json_data=json.loads(self.THREADLIST))

        fake_session.routes = [
            ("GET", is_home_url, dtsg_handler),
            ("GET", "threadlist_info.php", threadlist_handler),
        ]
        result = await make_client().get_thread_list(limit=5)
        assert result["total"] == 1
        assert result["conversations"][0]["thread_id"] == "t1"

    @pytest.mark.asyncio
    async def test_get_thread(self, fake_session):
        def dtsg_handler(method, url, params, data, files):
            return FakeResponse(status_code=200, text=HOME_HTML, url="https://www.facebook.com/")

        def thread_handler(method, url, params, data, files):
            return FakeResponse(status_code=200, json_data=json.loads(self.THREAD))

        fake_session.routes = [
            ("GET", is_home_url, dtsg_handler),
            ("GET", "thread_info.php", thread_handler),
        ]
        result = await make_client().get_thread("t1")
        assert result["messages"][0]["body"] == "hey"

    @pytest.mark.asyncio
    async def test_send_message(self, fake_session):
        def dtsg_handler(method, url, params, data, files):
            return FakeResponse(status_code=200, text=HOME_HTML, url="https://www.facebook.com/")

        def send_handler(method, url, params, data, files):
            assert method == "POST"
            assert data["threads[thread_fbid]"] == json.dumps(["t1"])
            return FakeResponse(status_code=200, json_data={"success": True, "message_id": "m9"})

        fake_session.routes = [
            ("GET", is_home_url, dtsg_handler),
            ("POST", "send_messages.php", send_handler),
        ]
        result = await make_client().send_message("t1", "hello")
        assert result["success"] is True
        assert result["message_id"] == "m9"


class TestWrites:
    @pytest.mark.asyncio
    async def test_create_post(self, fake_session):
        def dtsg_handler(method, url, params, data, files):
            return FakeResponse(status_code=200, text=HOME_HTML, url="https://www.facebook.com/")

        def post_handler(method, url, params, data, files):
            assert data["status"] == "hello world"
            return FakeResponse(status_code=200, json_data={"success": True, "post_id": "p1"})

        fake_session.routes = [
            ("GET", is_home_url, dtsg_handler),
            ("POST", "updatestatus.php", post_handler),
        ]
        result = await make_client().create_post("hello world")
        assert result["success"] is True
        assert result["post_id"] == "p1"

    @pytest.mark.asyncio
    async def test_create_comment(self, fake_session):
        def dtsg_handler(method, url, params, data, files):
            return FakeResponse(status_code=200, text=HOME_HTML, url="https://www.facebook.com/")

        def comment_handler(method, url, params, data, files):
            assert data["comment_text"] == "nice"
            return FakeResponse(status_code=200, json_data={"success": True, "comment_id": "c1"})

        fake_session.routes = [
            ("GET", is_home_url, dtsg_handler),
            ("POST", "ufi_composer.php", comment_handler),
        ]
        result = await make_client().create_comment("p1", "nice")
        assert result["success"] is True
        assert result["comment_id"] == "c1"

    @pytest.mark.asyncio
    async def test_get_post_comments(self, fake_session):
        def dtsg_handler(method, url, params, data, files):
            return FakeResponse(status_code=200, text=HOME_HTML, url="https://www.facebook.com/")

        def comments_handler(method, url, params, data, files):
            return FakeResponse(
                status_code=200,
                json_data={
                    "payload": {
                        "comments": [
                            {"id": "c1", "author": {"name": "Alice"}, "body": {"text": "hi"}}
                        ]
                    }
                },
            )

        fake_session.routes = [
            ("GET", is_home_url, dtsg_handler),
            ("GET", "ufi_realtime.php", comments_handler),
        ]
        result = await make_client().get_post_comments("p1")
        assert result["comments"][0]["author"] == "Alice"

    @pytest.mark.asyncio
    async def test_get_post_comments_falls_back_to_story_page(self, fake_session):
        def dtsg_handler(method, url, params, data, files):
            return FakeResponse(status_code=200, text=HOME_HTML, url="https://www.facebook.com/")

        def ufi_handler(method, url, params, data, files):
            return FakeResponse(status_code=404, text="Not Found")

        def story_handler(method, url, params, data, files):
            return FakeResponse(
                status_code=200,
                text=(
                    '<script type="application/json">'
                    + json.dumps(
                        {
                            "feedback": {
                                "comment_rendering_instance": {"comments": {"total_count": 16}}
                            }
                        }
                    )
                    + "</script>"
                ),
                url="https://www.facebook.com/story.php",
            )

        fake_session.routes = [
            ("GET", is_home_url, dtsg_handler),
            ("GET", "ufi_realtime.php", ufi_handler),
            ("GET", "story.php", story_handler),
        ]
        result = await make_client().get_post_comments("123_456")
        assert result["comment_count"] == 16
        assert result["source"] == "story_page"
        assert "official API" in result["note"]


class TestDtsgRefresh:
    """fb_dtsg rotation: stale token is refreshed once and the write retried."""

    @pytest.mark.asyncio
    async def test_send_message_refreshes_dtsg_on_bad_token(self, fake_session):
        calls = {"send": 0, "home": 0}

        def dtsg_handler(method, url, params, data, files):
            calls["home"] += 1
            return FakeResponse(status_code=200, text=HOME_HTML, url="https://www.facebook.com/")

        def send_handler(method, url, params, data, files):
            calls["send"] += 1
            if calls["send"] == 1:
                return FakeResponse(
                    status_code=200,
                    json_data={"error": {"error_msg": "Invalid token"}},
                )
            return FakeResponse(status_code=200, json_data={"success": True, "message_id": "m9"})

        fake_session.routes = [
            ("GET", is_home_url, dtsg_handler),
            ("POST", "send_messages.php", send_handler),
        ]
        result = await make_client().send_message("t1", "hello")
        assert result["success"] is True
        assert result["message_id"] == "m9"
        # First attempt rejected; token refetched; second attempt succeeded.
        assert calls["send"] == 2
        assert calls["home"] >= 2

    @pytest.mark.asyncio
    async def test_send_message_gives_up_after_two_bad_token_attempts(self, fake_session):
        calls = {"send": 0}

        def dtsg_handler(method, url, params, data, files):
            return FakeResponse(status_code=200, text=HOME_HTML, url="https://www.facebook.com/")

        def send_handler(method, url, params, data, files):
            calls["send"] += 1
            return FakeResponse(
                status_code=200,
                json_data={"error": {"error_msg": "Invalid token"}},
            )

        fake_session.routes = [
            ("GET", is_home_url, dtsg_handler),
            ("POST", "send_messages.php", send_handler),
        ]
        client = make_client()
        with pytest.raises(ScrapingError):
            await client.send_message("t1", "hello")
        assert calls["send"] == 2

    @pytest.mark.asyncio
    async def test_create_post_refreshes_dtsg_on_bad_token(self, fake_session):
        calls = {"post": 0}

        def dtsg_handler(method, url, params, data, files):
            return FakeResponse(status_code=200, text=HOME_HTML, url="https://www.facebook.com/")

        def post_handler(method, url, params, data, files):
            calls["post"] += 1
            if calls["post"] == 1:
                return FakeResponse(
                    status_code=200,
                    json_data={"success": False, "error": "invalid token"},
                )
            return FakeResponse(status_code=200, json_data={"success": True, "post_id": "p2"})

        fake_session.routes = [
            ("GET", is_home_url, dtsg_handler),
            ("POST", "updatestatus.php", post_handler),
        ]
        result = await make_client().create_post("hello world")
        assert result["success"] is True
        assert result["post_id"] == "p2"
        assert calls["post"] == 2

    @pytest.mark.asyncio
    async def test_send_message_404_surfaces_decommissioned_without_retry(self, fake_session):
        """Legacy 404 must translate to an actionable message and NOT retry/refresh."""
        calls = {"send": 0, "home": 0}

        def dtsg_handler(method, url, params, data, files):
            calls["home"] += 1
            return FakeResponse(status_code=200, text=HOME_HTML, url="https://www.facebook.com/")

        def send_handler(method, url, params, data, files):
            calls["send"] += 1
            return FakeResponse(status_code=404, text="Not Found")

        fake_session.routes = [
            ("GET", is_home_url, dtsg_handler),
            ("POST", "send_messages.php", send_handler),
        ]
        client = make_client()
        with pytest.raises(ScrapingError) as excinfo:
            await client.send_message("t1", "hello")
        assert "decommissioned" in str(excinfo.value)
        assert "official Messenger API" in str(excinfo.value)
        # No retry on the 404 (it is not a token problem) and no dtsg refresh.
        assert calls["send"] == 1
        assert calls["home"] == 1

    @pytest.mark.asyncio
    async def test_create_post_404_surfaces_decommissioned(self, fake_session):
        def dtsg_handler(method, url, params, data, files):
            return FakeResponse(status_code=200, text=HOME_HTML, url="https://www.facebook.com/")

        def post_handler(method, url, params, data, files):
            return FakeResponse(status_code=404, text="Not Found")

        fake_session.routes = [
            ("GET", is_home_url, dtsg_handler),
            ("POST", "updatestatus.php", post_handler),
        ]
        client = make_client()
        with pytest.raises(ScrapingError) as excinfo:
            await client.create_post("hello")
        assert "decommissioned" in str(excinfo.value)
        assert "Graph API" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_upload_photo_404_surfaces_decommissioned(self, fake_session, tmp_path):

        img = tmp_path / "p.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)

        def dtsg_handler(method, url, params, data, files):
            return FakeResponse(status_code=200, text=HOME_HTML, url="https://www.facebook.com/")

        def upload_handler(method, url, params, data, files):
            return FakeResponse(status_code=404, text="Not Found")

        fake_session.routes = [
            ("GET", is_home_url, dtsg_handler),
            ("POST", "attachment/create/upload", upload_handler),
        ]
        client = make_client()
        with pytest.raises(ScrapingError) as excinfo:
            await client.upload_photo(str(img))
        assert "decommissioned" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_real_failure_not_retried(self, fake_session):
        calls = {"send": 0}

        def dtsg_handler(method, url, params, data, files):
            return FakeResponse(status_code=200, text=HOME_HTML, url="https://www.facebook.com/")

        def send_handler(method, url, params, data, files):
            calls["send"] += 1
            return FakeResponse(
                status_code=200,
                json_data={"error": {"error_msg": "Message too long"}},
            )

        fake_session.routes = [
            ("GET", is_home_url, dtsg_handler),
            ("POST", "send_messages.php", send_handler),
        ]
        client = make_client()
        with pytest.raises(ScrapingError):
            await client.send_message("t1", "hello")
        # Non-token failures are not retried.
        assert calls["send"] == 1


class TestPacing:
    @pytest.mark.asyncio
    async def test_pacing_skips_when_disabled(self, fake_session, monkeypatch):
        monkeypatch.setattr("facebook_mcp_server.scraping.api_client._WRITE_PACING", False)

        def dtsg_handler(method, url, params, data, files):
            return FakeResponse(status_code=200, text=HOME_HTML, url="https://www.facebook.com/")

        def send_handler(method, url, params, data, files):
            return FakeResponse(status_code=200, json_data={"success": True})

        fake_session.routes = [
            ("GET", is_home_url, dtsg_handler),
            ("POST", "send_messages.php", send_handler),
        ]
        client = make_client()
        client._write_pace = False
        result = await client.send_message("t1", "hello")
        assert result["success"] is True


class TestParsersIntegration:
    def test_extract_shortcode_helpers_importable(self):
        # Ensure the module-level parser helpers referenced by the client exist.
        assert hasattr(parsers, "extract_user_id_from_html")
        assert hasattr(parsers, "extract_display_name")
        assert hasattr(parsers, "extract_username_from_html")
        assert hasattr(parsers, "parse_comments")
