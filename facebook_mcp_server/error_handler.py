"""
Centralized error handling for the Facebook MCP Server.

``raise_tool_error()`` maps known exception types to user-friendly FastMCP
``ToolError`` messages. Unknown exceptions are re-raised as ``ToolError``
with the original type name for debuggability.
"""

from __future__ import annotations

import logging
from typing import NoReturn

from fastmcp.exceptions import ToolError

from facebook_mcp_server.core import (
    AuthenticationError,
    LoginWallError,
    NetworkError,
    PageNotFoundError,
    RateLimitError,
    ScrapingError,
)
from facebook_mcp_server.error_diagnostics import (
    build_issue_diagnostics,
    format_tool_error_with_diagnostics,
)
from facebook_mcp_server.exceptions import (
    AuthenticationBootstrapFailedError,
    AuthenticationInProgressError,
    AuthenticationStartedError,
    ConfirmationRequiredError,
    CredentialsNotFoundError,
    DockerHostLoginRequiredError,
    FacebookMCPError,
    PageNotConfiguredError,
    SessionExpiredError,
)

logger = logging.getLogger(__name__)


def _raise_with_diagnostics(exception: Exception, message: str, context: str) -> NoReturn:
    try:
        diagnostics = build_issue_diagnostics(exception, context=context)
    except Exception:
        logger.debug("Could not build issue diagnostics", exc_info=True)
        diagnostics = None
    if diagnostics is not None:
        message = format_tool_error_with_diagnostics(message, diagnostics)
    raise ToolError(message) from exception


def raise_tool_error(exception: Exception, context: str = "") -> NoReturn:
    """Raise a ``ToolError`` with a user-friendly message for *exception*.

    Args:
        exception: The exception that occurred
        context: Optional context about which tool failed (for logs)

    Raises:
        ToolError: Always.
    """
    ctx = f" in {context}" if context else ""

    if isinstance(exception, CredentialsNotFoundError):
        logger.warning("Credentials not found%s: %s", ctx, exception)
        _raise_with_diagnostics(
            exception,
            "No Facebook session found. Run `facebook-lyr --login` to import "
            "browser cookies, or set FACEBOOK_COOKIES.",
            context,
        )

    elif isinstance(exception, PageNotConfiguredError):
        logger.warning("Page not configured%s: %s", ctx, exception)
        raise ToolError(str(exception)) from exception

    elif isinstance(exception, SessionExpiredError):
        logger.warning("Session expired%s: %s", ctx, exception)
        _raise_with_diagnostics(
            exception,
            "Facebook session expired. Run `facebook-lyr --login` to re-import browser cookies.",
            context,
        )

    elif isinstance(exception, AuthenticationStartedError):
        logger.info("Authentication started%s: %s", ctx, exception)
        raise ToolError(str(exception)) from exception

    elif isinstance(exception, AuthenticationInProgressError):
        logger.info("Authentication in progress%s: %s", ctx, exception)
        raise ToolError(str(exception)) from exception

    elif isinstance(exception, AuthenticationBootstrapFailedError):
        logger.warning("Authentication bootstrap failed%s: %s", ctx, exception)
        raise ToolError(str(exception)) from exception

    elif isinstance(exception, DockerHostLoginRequiredError):
        logger.warning("Docker host login required%s: %s", ctx, exception)
        raise ToolError(str(exception)) from exception

    elif isinstance(exception, (AuthenticationError, LoginWallError)):
        logger.warning("Authentication failed%s: %s", ctx, exception)
        _raise_with_diagnostics(
            exception,
            f"Facebook session problem: {exception}",
            context,
        )

    elif isinstance(exception, RateLimitError):
        wait = getattr(exception, "suggested_wait_time", 300)
        logger.warning("Rate limit%s: %s (wait=%ds)", ctx, exception, wait)
        raise ToolError(
            f"Facebook rate limit detected. Wait {wait} seconds before trying again."
        ) from exception

    elif isinstance(exception, PageNotFoundError):
        logger.warning("Page not found%s: %s", ctx, exception)
        _raise_with_diagnostics(
            exception,
            f"Not found: {exception}",
            context,
        )

    elif isinstance(exception, NetworkError):
        logger.warning("Network error%s: %s", ctx, exception)
        _raise_with_diagnostics(
            exception,
            f"Network error talking to Facebook: {exception}",
            context,
        )

    elif isinstance(exception, ScrapingError):
        logger.warning("Scraping error%s: %s", ctx, exception)
        _raise_with_diagnostics(
            exception,
            f"Scraping failed: {exception}",
            context,
        )

    elif isinstance(exception, ConfirmationRequiredError):
        logger.warning("Write action blocked%s: %s", ctx, exception)
        raise ToolError(str(exception)) from exception

    elif isinstance(exception, FacebookMCPError):
        logger.warning("Facebook MCP error%s: %s", ctx, exception)
        raise ToolError(str(exception)) from exception

    else:
        logger.error("Unexpected error%s: %s", ctx, exception, exc_info=exception)
        raise ToolError(f"Unexpected error: {type(exception).__name__}: {exception}") from exception
