"""Tests for exception hierarchies (core + MCP layers)."""

import pytest

from facebook_mcp_server.core import (
    AuthenticationError,
    FacebookScraperException,
    LoginWallError,
    NetworkError,
    PageNotFoundError,
    RateLimitError,
    ScrapingError,
)
from facebook_mcp_server.exceptions import (
    AuthenticationBootstrapFailedError,
    AuthenticationInProgressError,
    AuthenticationStartedError,
    CredentialsNotFoundError,
    DockerHostLoginRequiredError,
    FacebookMCPError,
    PageNotConfiguredError,
    SessionExpiredError,
)


class TestCoreExceptions:
    def test_hierarchy(self):
        for exc in (
            AuthenticationError,
            LoginWallError,
            NetworkError,
            PageNotFoundError,
            RateLimitError,
            ScrapingError,
        ):
            assert issubclass(exc, FacebookScraperException)

    def test_rate_limit_carries_wait_time(self):
        exc = RateLimitError("slow down", suggested_wait_time=120)
        assert exc.suggested_wait_time == 120

    def test_rate_limit_default_wait(self):
        exc = RateLimitError("slow down")
        assert exc.suggested_wait_time == 300


class TestMCPExceptions:
    def test_hierarchy(self):
        for exc in (
            CredentialsNotFoundError,
            SessionExpiredError,
            PageNotConfiguredError,
            AuthenticationStartedError,
            AuthenticationInProgressError,
            AuthenticationBootstrapFailedError,
            DockerHostLoginRequiredError,
        ):
            assert issubclass(exc, FacebookMCPError)

    def test_session_expired_default_message(self):
        exc = SessionExpiredError()
        assert "--login" in str(exc)

    def test_session_expired_custom_message(self):
        exc = SessionExpiredError("custom message")
        assert str(exc) == "custom message"

    def test_page_not_configured_default_message(self):
        exc = PageNotConfiguredError()
        assert "Page" in str(exc)

    def test_credentials_not_found(self):
        with pytest.raises(FacebookMCPError):
            raise CredentialsNotFoundError("nothing here")
