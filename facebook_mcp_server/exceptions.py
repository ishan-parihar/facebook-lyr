"""
Custom exceptions for the Facebook MCP Server (MCP-facing layer).

Hierarchical exception types for the server lifecycle: missing credentials,
expired sessions, unconfigured Page selection, and the bootstrap/login flow.
Scraping-level errors live in ``facebook_mcp_server.core.exceptions``.
"""


class FacebookMCPError(Exception):
    """Base exception for the Facebook MCP Server."""


class CredentialsNotFoundError(FacebookMCPError):
    """No Facebook session is available (neither env cookies nor saved profile)."""


class SessionExpiredError(FacebookMCPError):
    """The stored cookie session is invalid or expired."""

    def __init__(self, message: str | None = None):
        default_msg = (
            "Facebook session is invalid or expired.\n\n"
            "To fix this:\n"
            "  Run `facebook-lyr --login` to import fresh browser cookies, or "
            "set FACEBOOK_COOKIES and retry."
        )
        super().__init__(message or default_msg)


class PageNotConfiguredError(FacebookMCPError):
    """No Page is selected, but the tool requires one."""

    def __init__(self, message: str | None = None):
        default_msg = (
            "No Facebook Page is selected for this operation.\n\n"
            "To fix this:\n"
            "  Run `facebook-lyr set_active_page --page_id <ID>`, or use the "
            "page whose cookies are logged in (c_user identifies the account)."
        )
        super().__init__(message or default_msg)


class ConfirmationRequiredError(FacebookMCPError):
    """A write tool was called without the explicit ``confirm=true`` gate."""


def require_confirm(flag: bool, message: str) -> None:
    """Raise :class:`ConfirmationRequiredError` unless *flag* is true.

    Centralizes the write-tool safety gate so every guarded tool raises the
    same exception type and produces a clean, traceback-free ToolError.
    """
    if not flag:
        raise ConfirmationRequiredError(message)


class AuthenticationStartedError(FacebookMCPError):
    """A login flow has been started; the agent should retry shortly."""


class AuthenticationInProgressError(FacebookMCPError):
    """A login flow is already in progress; wait and retry."""


class AuthenticationBootstrapFailedError(FacebookMCPError):
    """The interactive login flow failed to complete."""


class DockerHostLoginRequiredError(FacebookMCPError):
    """Running inside Docker but no session was mounted/created on the host."""
