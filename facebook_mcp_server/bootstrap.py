"""Managed bootstrap for Facebook session authentication.

No browser setup is required.  The only precondition for API calls is a valid
Facebook session cookie file on disk (``~/.facebook-lyr/profile/cookies.json``)
containing at minimum ``c_user`` and ``xs``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import NoReturn

from fastmcp import Context

from facebook_mcp_server.authentication import get_authentication_source
from facebook_mcp_server.common_utils import secure_mkdir, utcnow_iso
from facebook_mcp_server.config.loaders import EnvironmentKeys
from facebook_mcp_server.drivers.browser import get_profile_dir
from facebook_mcp_server.exceptions import (
    AuthenticationBootstrapFailedError,
    AuthenticationInProgressError,
    AuthenticationStartedError,
    DockerHostLoginRequiredError,
)
from facebook_mcp_server.session_state import (
    auth_root_dir,
    portable_cookie_path,
    profile_exists,
    source_state_path,
    write_source_state,
)

logger = logging.getLogger(__name__)

_INVALID_STATE_PREFIX = "invalid-state-"

FACEBOOK_COOKIES_VAR = EnvironmentKeys.FACEBOOK_COOKIES


def _ensure_cookies_from_env() -> None:
    """If ``FACEBOOK_COOKIES`` is set, write cookies.json + source-state.json so
    the existing file-based bootstrap checks pass transparently.

    This is the primary flow for AI agents running in headless environments.
    """
    raw = os.environ.get(FACEBOOK_COOKIES_VAR)
    if not raw:
        return

    profile_dir = get_profile_dir()
    cookie_path = portable_cookie_path(profile_dir)

    # Parse env var — accepts flat dict or list-of-dicts format
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.error("FACEBOOK_COOKIES is not valid JSON; ignoring")
        return

    if isinstance(data, dict):
        cookies = {k: v for k, v in data.items() if k and v}
    elif isinstance(data, list):
        cookies = {c["name"]: c["value"] for c in data if "name" in c and "value" in c}
    else:
        logger.error("FACEBOOK_COOKIES must be a JSON object or array; ignoring")
        return

    if "c_user" not in cookies or "xs" not in cookies:
        logger.error("FACEBOOK_COOKIES must include 'c_user' and 'xs'; ignoring")
        return

    # Write cookies.json in the server's portable format
    cookie_data = {
        "cookies": [
            {
                "name": name,
                "value": value,
                "domain": ".facebook.com",
                "path": "/",
                "secure": True,
                "expires": -1,
            }
            for name, value in cookies.items()
        ],
        "imported_from": "env",
    }
    cookie_path.parent.mkdir(parents=True, exist_ok=True)
    cookie_path.write_text(json.dumps(cookie_data, indent=2))
    cookie_path.chmod(0o600)
    logger.info("Wrote %d cookies from FACEBOOK_COOKIES env var", len(cookies))

    # Ensure profile + auth directories exist so file-based bootstrap checks
    # (profile_exists + source_state) pass transparently. A marker file makes
    # the empty profile dir count as "existing" for profile_exists().
    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / ".env-session").write_text("")
    auth_root = auth_root_dir(profile_dir)
    auth_root.mkdir(parents=True, exist_ok=True)

    # Write a minimal source-state.json so bootstrap checks pass
    source_state_path(profile_dir).parent.mkdir(parents=True, exist_ok=True)
    write_source_state(source_profile_dir=profile_dir, preferred_browser="env")


class RuntimePolicy(str, Enum):
    MANAGED = "managed"
    DOCKER = "docker"


class SetupState(str, Enum):
    IDLE = "not_started"
    READY = "ready"
    FAILED = "failed"


class AuthState(str, Enum):
    IDLE = "idle"
    STARTING = "starting_login"
    IN_PROGRESS = "login_in_progress"
    READY = "auth_ready"
    FAILED = "failed"


@dataclass(slots=True)
class BootstrapState:
    runtime_policy: RuntimePolicy | None = None
    setup_state: SetupState = SetupState.IDLE
    auth_state: AuthState = AuthState.IDLE
    last_error: str | None = None
    setup_started_at: str | None = None
    setup_completed_at: str | None = None
    auth_started_at: str | None = None
    auth_completed_at: str | None = None
    setup_task: asyncio.Task[None] | None = None
    login_task: asyncio.Task[None] | None = None
    initialized: bool = False


_state = BootstrapState()
_lock = asyncio.Lock()


def reset_bootstrap_for_testing() -> None:
    """Reset bootstrap singleton state for test isolation."""
    global _state, _lock
    for task in (_state.setup_task, _state.login_task):
        if task is not None and not task.done():
            task.cancel()
    _state = BootstrapState()
    _lock = asyncio.Lock()


def get_runtime_policy() -> RuntimePolicy:
    """Return the active bootstrap runtime policy."""
    if _state.runtime_policy is not None:
        return _state.runtime_policy
    return (
        RuntimePolicy.DOCKER if _get_runtime_id().endswith("-container") else RuntimePolicy.MANAGED
    )


def _get_runtime_id() -> str:
    from facebook_mcp_server.session_state import get_runtime_id

    return get_runtime_id()


def initialize_bootstrap(runtime_policy: RuntimePolicy | str | None = None) -> None:
    """Initialize bootstrap state."""
    if _state.initialized:
        return

    # If FACEBOOK_COOKIES env var is set, write the cookie profile before
    # any tool call so file-based bootstrap checks pass automatically.
    _ensure_cookies_from_env()

    _state.runtime_policy = RuntimePolicy(runtime_policy or get_runtime_policy())
    _state.initialized = True


def get_bootstrap_state() -> BootstrapState:
    """Return current bootstrap state."""
    return _state


async def start_background_browser_setup_if_needed() -> None:
    """No-op: there is no browser to set up."""
    initialize_bootstrap()
    if get_runtime_policy() != RuntimePolicy.MANAGED:
        return
    async with _lock:
        _state.setup_state = SetupState.READY
        _state.setup_completed_at = _state.setup_completed_at or utcnow_iso()


def browser_setup_ready() -> bool:
    """No browser setup required.  Always ``True``."""
    return True


def configure_browser_environment() -> Path:
    """No-op: there is no browser environment to configure."""
    return get_profile_dir()


def ensure_browser_installed() -> None:
    """No-op: there is no browser to install."""


async def ensure_tool_ready_or_raise(tool_name: str, ctx: Context | None = None) -> None:
    """Gate scrape/search tools on authentication readiness (cookie check)."""
    initialize_bootstrap()

    if get_runtime_policy() == RuntimePolicy.DOCKER:
        _raise_if_docker_auth_missing()
        return

    if _auth_ready():
        _state.auth_state = AuthState.READY
        return

    await _start_login_if_needed(ctx)


def _raise_if_docker_auth_missing() -> None:
    if _auth_ready():
        return
    raise DockerHostLoginRequiredError(
        "No valid Facebook session is available in Docker. "
        "Run --login on the host machine to create a session, "
        "then retry this tool."
    )


def _auth_ready() -> bool:
    profile_dir = get_profile_dir()
    return (
        profile_exists(profile_dir)
        and portable_cookie_path(profile_dir).exists()
        and source_state_path(profile_dir).exists()
        and _has_source_state()
    )


def _has_source_state() -> bool:
    try:
        get_authentication_source()
    except Exception:
        return False
    return True


async def _start_login_if_needed(ctx: Context | None = None) -> None:
    async with _lock:
        if _auth_ready():
            _state.auth_state = AuthState.READY
            return

        if _state.login_task is not None and not _state.login_task.done():
            if ctx is not None:
                await ctx.report_progress(
                    progress=25,
                    total=100,
                    message="Facebook login already in progress",
                )
            raise AuthenticationInProgressError(
                "No valid Facebook session is available yet. "
                "Facebook login is already in progress. "
                "Complete login there, then retry this tool."
            )

        _move_invalid_auth_state_aside()
        _state.auth_state = AuthState.STARTING
        _state.auth_started_at = utcnow_iso()
        _state.last_error = None
        _state.auth_completed_at = None
        _state.login_task = asyncio.create_task(_run_login_flow(), name="facebook-login")

    if ctx is not None:
        await ctx.report_progress(
            progress=25,
            total=100,
            message="Facebook cookie import started",
        )
    raise AuthenticationStartedError(
        "No valid Facebook session was found. "
        "Cookie import has been started. "
        "Pick a browser with a logged-in facebook.com session, then retry this tool."
    )


async def start_login_if_needed(ctx: Context | None = None) -> None:
    """Public wrapper for starting the shared login workflow."""
    await _start_login_if_needed(ctx)


async def invalidate_auth_and_trigger_relogin(
    ctx: Context | None = None,
) -> NoReturn:
    """Force-invalidate stale auth state and trigger interactive login."""
    logger.warning("Invalidating stale auth state and triggering re-login")
    async with _lock:
        if _state.login_task is not None and not _state.login_task.done():
            if ctx is not None:
                await ctx.report_progress(
                    progress=25,
                    total=100,
                    message="Facebook login already in progress",
                )
            raise AuthenticationInProgressError(
                "No valid Facebook session is available yet. "
                "Facebook login is already in progress. "
                "Complete login there, then retry this tool."
            )

        _force_move_auth_state_aside()
        _state.auth_state = AuthState.STARTING
        _state.auth_started_at = utcnow_iso()
        _state.last_error = None
        _state.auth_completed_at = None
        _state.login_task = asyncio.create_task(_run_login_flow(), name="facebook-login")

    if ctx is not None:
        await ctx.report_progress(
            progress=25,
            total=100,
            message="Facebook cookie import started",
        )
    raise AuthenticationStartedError(
        "Session expired. "
        "Cookie import has been started. "
        "Pick a browser with a logged-in facebook.com session, then retry this tool."
    )


def _move_auth_state_aside(*, force: bool = False) -> None:
    profile_dir = get_profile_dir()
    targets = [
        profile_dir,
        portable_cookie_path(profile_dir),
        source_state_path(profile_dir),
    ]
    existing = [target for target in targets if target.exists()]
    if not existing:
        return
    if not force and _auth_ready():
        return

    auth_root = auth_root_dir(profile_dir)
    backup_dir = auth_root / f"{_INVALID_STATE_PREFIX}{utcnow_iso().replace(':', '-')}"
    secure_mkdir(backup_dir)
    for target in existing:
        shutil.move(str(target), str(backup_dir / target.name))


def _force_move_auth_state_aside() -> None:
    _move_auth_state_aside(force=True)


def _move_invalid_auth_state_aside() -> None:
    _move_auth_state_aside(force=False)


async def _run_login_flow() -> None:
    """Run interactive cookie-import login flow."""
    _state.auth_state = AuthState.IN_PROGRESS

    from facebook_mcp_server.cookie_import import import_cookies_interactive

    success = import_cookies_interactive()
    if not success:
        raise AuthenticationBootstrapFailedError(
            "Facebook login was not completed. "
            "Retry the tool call to reopen the browser and continue setup."
        )
