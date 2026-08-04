"""
Authentication logic for the Facebook MCP Server.

Handles Facebook session management with a persistent cookie profile.
"""

import logging
import shutil
from pathlib import Path

from facebook_mcp_server.exceptions import CredentialsNotFoundError
from facebook_mcp_server.session_state import (
    clear_auth_state as clear_all_auth_state,
)
from facebook_mcp_server.session_state import (
    get_source_profile_dir,
    load_source_state,
    portable_cookie_path,
    profile_exists,
    source_state_path,
)

logger = logging.getLogger(__name__)


def get_authentication_source() -> bool:
    """
    Check if authentication is available via persistent cookie profile.

    Returns:
        True if a valid source profile exists

    Raises:
        CredentialsNotFoundError: If no authentication method available
    """
    profile_dir = get_source_profile_dir()
    cookies_path = portable_cookie_path(profile_dir)
    source_state = load_source_state(profile_dir)
    if profile_exists(profile_dir) and cookies_path.exists() and source_state:
        logger.info("Using source profile from %s", profile_dir)
        return True

    if profile_exists(profile_dir) or cookies_path.exists():
        raise CredentialsNotFoundError(
            "Facebook source session metadata is missing or incomplete.\n\n"
            f"Expected source metadata: {source_state_path(profile_dir)}\n"
            f"Expected portable cookies: {cookies_path}\n\n"
            "Run with --login to create a fresh source session generation."
        )

    raise CredentialsNotFoundError(
        "No Facebook source session found.\n\n"
        "Options:\n"
        "  1. Run with --login to import browser cookies (recommended)\n"
        "  2. Set FACEBOOK_COOKIES to a JSON object containing c_user and xs\n\n"
        "For Docker users:\n"
        "  Create profile on host first: uv run -m facebook_mcp_server --login\n"
        "  Then mount into Docker: -v ~/.facebook-lyr:/home/pwuser/.facebook-lyr"
    )


def clear_profile(profile_dir: Path | None = None) -> bool:
    """
    Clear stored browser profile directory.

    Args:
        profile_dir: Path to profile directory

    Returns:
        True if clearing was successful
    """
    if profile_dir is None:
        profile_dir = get_source_profile_dir()

    if profile_dir.exists():
        try:
            shutil.rmtree(profile_dir)
            logger.info(f"Profile cleared from {profile_dir}")
            return True
        except OSError as e:
            logger.warning(f"Could not clear profile: {e}")
            return False
    return True


def clear_auth_state(profile_dir: Path | None = None) -> bool:
    """Clear source session artifacts and all derived runtime sessions."""
    return clear_all_auth_state(profile_dir or get_source_profile_dir())
