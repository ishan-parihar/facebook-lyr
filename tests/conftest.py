import json

import pytest


def is_home_url(url: str) -> bool:
    """Route predicate matching exactly the facebook.com homepage."""
    return url.rstrip("/") == "https://www.facebook.com"


@pytest.fixture(autouse=True)
def reset_singletons():
    """Reset global state for test isolation."""
    from facebook_mcp_server.bootstrap import reset_bootstrap_for_testing
    from facebook_mcp_server.config import reset_config
    from facebook_mcp_server.scraping import api_client

    reset_bootstrap_for_testing()
    reset_config()
    api_client.set_write_pacing(False)
    yield
    reset_bootstrap_for_testing()
    reset_config()
    api_client.set_write_pacing(True)


@pytest.fixture(autouse=True)
def isolate_profile_dir(tmp_path, monkeypatch):
    """Redirect the profile directory to tmp_path via USER_DATA_DIR."""
    fake_profile = tmp_path / "facebook-lyr" / "profile"
    monkeypatch.setenv("USER_DATA_DIR", str(fake_profile))
    monkeypatch.delenv("FACEBOOK_COOKIES", raising=False)
    monkeypatch.delenv("FACEBOOK_PREFERRED_BROWSER", raising=False)
    # run_tool_direct() sets this; ensure it never leaks into other tests.
    monkeypatch.delenv("FACEBOOK_MCP_TOOL_MODE", raising=False)
    # argparse (config loaders) would otherwise choke on pytest's sys.argv.
    monkeypatch.setattr("sys.argv", ["facebook-lyr"])

    from facebook_mcp_server.config import reset_config

    reset_config()
    return fake_profile


@pytest.fixture
def mock_context():
    """Mock FastMCP Context."""
    from unittest.mock import AsyncMock, MagicMock

    ctx = MagicMock()
    ctx.report_progress = AsyncMock()
    return ctx


# ── Fake curl_cffi session for client tests ────────────────────────────────


class FakeResponse:
    """Mimics the parts of a curl_cffi response used by the client."""

    def __init__(
        self,
        status_code: int = 200,
        text: str = "",
        json_data: dict | None = None,
        url: str = "",
        headers: dict | None = None,
        content: bytes = b"",
    ):
        self.status_code = status_code
        self._text = text
        self._json = json_data
        self.url = url
        self.headers = headers or {}
        self.content = content or text.encode()

    @property
    def text(self) -> str:
        return self._text

    def json(self):
        if self._json is not None:
            return self._json
        return json.loads(self._text)


class FakeSession:
    """Mimics curl_cffi AsyncSession with a route table for tests."""

    from typing import ClassVar

    routes: ClassVar[list] = []
    instances: ClassVar[list] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.calls: list[tuple] = []
        FakeSession.instances.append(self)

    async def request(self, method: str, url: str, params=None, data=None, files=None, **kwargs):
        self.calls.append((method, url, params, data))
        for route_method, route_match, handler in FakeSession.routes:
            if route_method != method:
                continue
            if callable(route_match):
                matched = route_match(url)
            else:
                matched = route_match in url
            if matched:
                result = handler(method, url, params, data, files)
                if hasattr(result, "__await__"):
                    result = await result
                return result
        return FakeResponse(status_code=404, text="no route")

    async def get(self, url: str, **kwargs):
        return await self.request("GET", url, **kwargs)

    @property
    def cookies(self):
        return None

    async def aclose(self):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.fixture
def fake_session(monkeypatch):
    """Patch the client's AsyncSession with FakeSession and return the class."""
    from facebook_mcp_server.scraping import api_client

    monkeypatch.setattr(api_client, "_AsyncSession", FakeSession)
    FakeSession.routes = []
    FakeSession.instances = []
    yield FakeSession
    FakeSession.routes = []
    FakeSession.instances = []
