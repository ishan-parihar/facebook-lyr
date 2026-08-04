"""
Issue diagnostics for tool errors — surfaces actionable hints alongside the
human-readable message so agents can self-recover.
"""

from __future__ import annotations

import logging
from typing import Any

from facebook_mcp_server.core import (
    AuthenticationError,
    NetworkError,
    PageNotFoundError,
    RateLimitError,
    ScrapingError,
)

logger = logging.getLogger(__name__)

_REMEDIES: dict[type, list[str]] = {
    AuthenticationError: [
        "Run `facebook-lyr --login` to import fresh browser cookies.",
        "Or set FACEBOOK_COOKIES to a JSON object containing c_user and xs.",
    ],
    RateLimitError: [
        "Slow down: Facebook is rate-limiting this session.",
        "Wait a few minutes before retrying, and reduce request bursts.",
    ],
    PageNotFoundError: [
        "Double-check the username/url — the profile may not exist or is private.",
    ],
    NetworkError: [
        "Check your network connection and proxy settings, then retry.",
    ],
    ScrapingError: [
        "Facebook may have changed its page structure. Retry once, then report.",
    ],
}


def build_issue_diagnostics(exception: Exception, context: str = "") -> dict[str, Any] | None:
    """Build a small diagnostics dict for *exception*, or None if not available."""
    try:
        remedies: list[str] = []
        for exc_type, hints in _REMEDIES.items():
            if isinstance(exception, exc_type):
                remedies.extend(hints)
        if not remedies:
            return None
        return {
            "error_type": type(exception).__name__,
            "context": context or "",
            "remedies": remedies,
        }
    except Exception:
        logger.debug("Could not build issue diagnostics", exc_info=True)
        return None


def format_tool_error_with_diagnostics(message: str, diagnostics: dict[str, Any] | None) -> str:
    """Append diagnostic hints to a tool error message."""
    if not diagnostics:
        return message
    remedies = diagnostics.get("remedies") or []
    if not remedies:
        return message
    hint = " | ".join(remedies)
    return f"{message}\nhelp: {hint}"
