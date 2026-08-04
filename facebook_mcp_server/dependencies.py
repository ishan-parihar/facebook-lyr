"""Dependency injection for MCP tools — creates a scrape client from saved cookies."""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, NoReturn

from fastmcp import Context

from facebook_mcp_server.bootstrap import (
    ensure_tool_ready_or_raise,
    invalidate_auth_and_trigger_relogin,
)
from facebook_mcp_server.core import AuthenticationError
from facebook_mcp_server.drivers.browser import get_profile_dir
from facebook_mcp_server.error_handler import raise_tool_error
from facebook_mcp_server.scraping import FacebookScrapeClient
from facebook_mcp_server.session_state import portable_cookie_path

logger = logging.getLogger(__name__)

_clients: set[FacebookScrapeClient] = set()
_clients_lock = threading.Lock()


async def handle_auth_error(
    error: AuthenticationError,
    ctx: Context | None,
) -> NoReturn:
    """Trigger interactive re-login."""
    logger.warning("Stale session detected; triggering re-login")
    await invalidate_auth_and_trigger_relogin(ctx)


async def get_ready_client(
    ctx: Context | None,
    *,
    tool_name: str,
    account_id: str | None = None,
) -> FacebookScrapeClient:
    """Run bootstrap gating, then create an authenticated scrape client.

    Args:
        ctx: MCP context
        tool_name: Name of the tool being executed
        account_id: Optional stored identity id to act as. When omitted,
            the active account is used if one is configured, falling back
            to the default profile session.

    Returns:
        Authenticated FacebookScrapeClient instance
    """
    try:
        await ensure_tool_ready_or_raise(tool_name, ctx)
        client = _build_api_client(account_id=account_id)
        return client
    except AuthenticationError as e:
        await handle_auth_error(e, ctx)
    except Exception as e:
        raise_tool_error(e, tool_name)


def _build_api_client(account_id: str | None = None) -> FacebookScrapeClient:
    """Load cookies (from a stored identity or the default profile) and build a client.

    Args:
        account_id: Optional stored identity id. When provided, that identity's
            cookie store is used. Otherwise the active stored account is used
            if any, falling back to the default profile session.
    """
    from facebook_mcp_server.multi_account import (
        get_account_cookies as get_stored_account_cookies,
    )
    from facebook_mcp_server.multi_account import (
        get_active_account as get_active_stored_account,
    )

    cookies: dict[str, str] | None = None
    source_label = ""
    raw: Any = None
    cookie_file: Path | None = None

    if account_id:
        cookies = get_stored_account_cookies(account_id)
        if not cookies:
            # A missing stored account is a config problem, not session expiry
            # — raising AuthenticationError here would trigger the re-login
            # flow and destroy the valid default session.
            from facebook_mcp_server.core import ScrapingError

            raise ScrapingError(
                f"Stored account {account_id} not found or no cookies. "
                "Add it first with add_account / import_account_from_browser."
            )
        source_label = f"account {account_id}"
        # Persist rotations back into the account cookie store.
        raw = dict(cookies)
        cookie_file = _account_cookies_path(account_id)
    else:
        active = get_active_stored_account()
        if active:
            stored = get_stored_account_cookies(active.account_id)
            if stored and "c_user" in stored and "xs" in stored:
                cookies = stored
                source_label = f"active account {active.name} ({active.account_id})"
                raw = dict(stored)
                cookie_file = _account_cookies_path(active.account_id)

    if cookies is None:
        profile_dir = get_profile_dir()
        cookie_file = portable_cookie_path(profile_dir)
        if not cookie_file.exists():
            raise AuthenticationError("No Facebook session found. Run with --login to create one.")
        raw = json.loads(cookie_file.read_text())
        if isinstance(raw, list):
            cookies = {c["name"]: c["value"] for c in raw if "name" in c and "value" in c}
        elif isinstance(raw, dict):
            cookies = {
                c["name"]: c["value"]
                for c in raw.get("cookies", [])
                if "name" in c and "value" in c
            } or {k: v for k, v in raw.items() if k != "imported_from"}
        else:
            cookies = {}
        source_label = str(cookie_file)

    if "c_user" not in cookies or "xs" not in cookies:
        raise AuthenticationError(
            "Saved cookies are missing c_user/xs. Run with --login to re-authenticate."
        )

    logger.info(
        "API client created from %s (%d cookies)",
        source_label,
        len(cookies),
    )
    sink = _make_cookie_sink(cookie_file, raw) if cookie_file is not None else None
    client = FacebookScrapeClient(cookies, cookie_sink=sink)
    with _clients_lock:
        _clients.add(client)
    return client


def _account_cookies_path(account_id: str) -> Path:
    """Return the cookie-store path for a stored identity."""
    from facebook_mcp_server.multi_account import account_cookies_path

    return account_cookies_path(account_id)


_FILE_LOCKS: dict[Path, threading.Lock] = {}


def _make_cookie_sink(cookie_file: Path, raw: Any) -> Callable[[dict[str, str]], None]:
    """Return a sink that writes rotated cookies back in the file's own format.

    Facebook periodically refreshes ``xs``/``fr``/``datr`` via Set-Cookie.
    Persisting those updates keeps the stored jar in sync with the live session
    so the next process doesn't present stale tokens. The original structure
    (list-of-dicts from a browser export, or the portable dict format) is
    preserved in place.
    """

    def sink(combined: dict[str, str]) -> None:
        lock = _FILE_LOCKS.setdefault(cookie_file, threading.Lock())
        with lock:
            if isinstance(raw, list):
                by_name = {c["name"]: c for c in raw if "name" in c}
                for name, value in combined.items():
                    if name in by_name:
                        by_name[name]["value"] = value
                existing = set(by_name)
                for name, value in combined.items():
                    if name not in existing:
                        raw.append({"name": name, "value": value})
            elif isinstance(raw, dict) and "cookies" in raw:
                by_name = {c["name"]: c for c in raw["cookies"] if "name" in c}
                for name, value in combined.items():
                    if name in by_name:
                        by_name[name]["value"] = value
            else:
                raw.update(combined)
            cookie_file.write_text(json.dumps(raw, indent=2))

    return sink


async def get_ready_posting_client(
    ctx: Context | None,
    *,
    tool_name: str,
    account_id: str | None = None,
) -> Any:
    """Get a posting client for write operations.

    Runs bootstrap gating, then builds a :class:`PostingClient` bound to the
    requested identity (explicit account > active account > default profile).

    Args:
        ctx: MCP context
        tool_name: Name of the tool being executed
        account_id: Optional stored identity id to post as

    Returns:
        Authenticated PostingClient instance
    """
    try:
        from facebook_mcp_server.posting.client import PostingClient

        await ensure_tool_ready_or_raise(tool_name, ctx)
        return PostingClient(account_id=account_id)
    except AuthenticationError as e:
        await handle_auth_error(e, ctx)
    except Exception as e:
        raise_tool_error(e, tool_name)


async def close_all_clients() -> int:
    """Close all active scrape clients (used by the close_session tool)."""
    with _clients_lock:
        clients = list(_clients)
        _clients.clear()
    closed = 0
    for client in clients:
        try:
            await client.close()
            closed += 1
        except Exception:
            logger.debug("Failed to close a scrape client", exc_info=True)
    return closed
