"""Tests for the centralized error handler."""

import pytest
from fastmcp.exceptions import ToolError

from facebook_mcp_server.core import (
    AuthenticationError,
    NetworkError,
    PageNotFoundError,
    RateLimitError,
    ScrapingError,
)
from facebook_mcp_server.error_handler import raise_tool_error
from facebook_mcp_server.exceptions import (
    CredentialsNotFoundError,
    PageNotConfiguredError,
    SessionExpiredError,
)


def _raise_and_catch(exc: Exception) -> str:
    with pytest.raises(ToolError) as excinfo:
        raise_tool_error(exc, "test_tool")
    return str(excinfo.value)


class TestMappings:
    def test_credentials_not_found(self):
        msg = _raise_and_catch(CredentialsNotFoundError("nope"))
        assert "--login" in msg

    def test_page_not_configured(self):
        msg = _raise_and_catch(PageNotConfiguredError())
        assert "Page" in msg

    def test_session_expired(self):
        msg = _raise_and_catch(SessionExpiredError())
        assert "--login" in msg

    def test_authentication_error(self):
        msg = _raise_and_catch(AuthenticationError("session invalid"))
        assert "session invalid" in msg
        assert "help:" in msg  # diagnostics appended

    def test_rate_limit(self):
        msg = _raise_and_catch(RateLimitError("limited", suggested_wait_time=60))
        assert "60" in msg

    def test_page_not_found(self):
        msg = _raise_and_catch(PageNotFoundError("no such page"))
        assert "no such page" in msg

    def test_network_error(self):
        msg = _raise_and_catch(NetworkError("timeout"))
        assert "timeout" in msg

    def test_scraping_error(self):
        msg = _raise_and_catch(ScrapingError("structure changed"))
        assert "structure changed" in msg

    def test_unknown_error(self):
        msg = _raise_and_catch(RuntimeError("boom"))
        assert "RuntimeError" in msg
        assert "boom" in msg
