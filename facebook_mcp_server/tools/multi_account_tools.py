"""
Multi-account management tools for the Facebook MCP Server.

Provides tools for AI agents to manage multiple Facebook identities (user
accounts and Pages), switch between them, and perform account-specific
operations — mirroring instagram-lyr's ``multi_account_tools.py``.

Switching the active identity changes which cookie set the scrape client
loads, so every read/write tool acts as the selected identity. Discovery of
which identities the session *could* switch to (Pages administered, linked
profiles) is available via ``list_switchable_accounts``.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastmcp import Context, FastMCP
from fastmcp.dependencies import CurrentContext
from pydantic import Field

from facebook_mcp_server.constants import TOOL_TIMEOUT_SECONDS

# Imported with a module alias: the @mcp.tool functions below share the same
# names (list_accounts, add_account, ...) and would otherwise shadow these
# module-level functions, causing tools to call themselves.
from facebook_mcp_server import multi_account as _ma
from facebook_mcp_server.tools._guard import tool_guard

logger = logging.getLogger(__name__)


def register_multi_account_tools(mcp: FastMCP) -> None:
    """Register all multi-account management tools with the MCP server."""

    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="List Facebook Accounts",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"account", "management"},
    )
    @tool_guard("list_accounts")
    async def list_accounts(ctx: Context = CurrentContext()) -> dict[str, Any]:
        """
        List all stored Facebook identities (cookie sets).

        Each stored identity has its own cookies (c_user/xs); the active one
        is what every tool acts as. Use `switch_active_account` to change it.

        Returns:
            Dict with accounts (id, name, username, identity_type, is_active,
            last_used), total_accounts, and active_account.
        """
        accounts = _ma.list_accounts()
        result: dict[str, Any] = {
            "accounts": [
                {
                    "account_id": acc.account_id,
                    "name": acc.name,
                    "username": acc.username,
                    "identity_type": acc.identity_type,
                    "is_active": acc.is_active,
                    "created_at": acc.created_at,
                    "last_used": acc.last_used,
                }
                for acc in accounts
            ],
            "total_accounts": len(accounts),
            "active_account": None,
        }
        active = _ma.get_active_account()
        if active is not None:
            result["active_account"] = active.account_id
        return result

    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="List Switchable Accounts",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"account", "discovery"},
    )
    @tool_guard("list_switchable_accounts")
    async def list_switchable_accounts(ctx: Context = CurrentContext()) -> dict[str, Any]:
        """
        Discover every identity the logged-in session can switch to.

        Reads Facebook's account switcher — the Pages you administer, linked
        profiles, and the user profile itself. This is the discovery source an
        agent uses to learn which identities exist for this session.

        Returns:
            Dict with accounts (id, name, typename, is_current) and total.
        """
        from facebook_mcp_server.dependencies import get_ready_client

        client = await get_ready_client(ctx, tool_name="list_switchable_accounts")
        return await client.get_switchable_accounts()

    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="Add Facebook Account",
        annotations={"destructiveHint": True, "openWorldHint": True},
        tags={"account", "management"},
    )
    @tool_guard("add_account")
    async def add_account(
        account_id: Annotated[str, Field(min_length=1, max_length=64)],
        name: Annotated[str, Field(min_length=1, max_length=255)],
        cookies: dict[str, str],
        username: Annotated[str | None, Field(max_length=255)] = None,
        identity_type: Annotated[str | None, Field(max_length=16)] = "user",
        set_as_active: bool = True,
        ctx: Context = CurrentContext(),
    ) -> dict[str, Any]:
        """
        Store a new Facebook identity's cookie set for agent use.

        The cookies dict must contain at least `c_user` and `xs` (a logged-in
        facebook.com session). After adding, all tools act as this identity
        when set_as_active is True (default).

        Args:
            account_id: The Facebook account/page id (numeric, from c_user)
            name: Display name for the identity
            cookies: Cookie dict with c_user + xs
            username: Optional vanity username
            identity_type: user, page, or profile
            set_as_active: Make this the active identity (default True)

        Returns:
            Account metadata including the account_id.
        """
        account = _ma.create_account(
            account_id=account_id,
            cookies=cookies,
            name=name,
            username=username or "",
            identity_type=identity_type or "user",
        )
        if set_as_active:
            _ma.set_default_account(account.account_id)
        return {
            "success": True,
            "account_id": account.account_id,
            "name": account.name,
            "is_active": account.is_active,
            "message": f"Account {name} added successfully",
        }

    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="Remove Facebook Account",
        annotations={"destructiveHint": True},
        tags={"account", "management"},
    )
    @tool_guard("remove_account")
    async def remove_account(
        account_id: Annotated[str, Field(min_length=1, max_length=64)],
        ctx: Context = CurrentContext(),
    ) -> dict[str, Any]:
        """
        Remove a stored Facebook identity's cookie set.

        Args:
            account_id: The account id to remove

        Returns:
            Success status and message.
        """
        del ctx
        account = _ma.get_account(account_id)
        if not account:
            return {"success": False, "message": f"Account {account_id} not found"}
        if _ma.delete_account(account_id):
            return {"success": True, "message": f"Account {account.name} removed successfully"}
        return {"success": False, "message": f"Failed to remove account {account.name}"}

    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="Switch Active Account",
        annotations={"destructiveHint": False, "openWorldHint": True},
        tags={"account", "management"},
    )
    @tool_guard("switch_active_account")
    async def switch_active_account(
        account_id: Annotated[str, Field(min_length=1, max_length=64)],
        ctx: Context = CurrentContext(),
    ) -> dict[str, Any]:
        """
        Switch which stored identity all tools act as.

        Subsequent tool calls load the selected identity's cookie set, so
        reads and writes happen as that identity.

        Args:
            account_id: The stored account id to activate

        Returns:
            Success status and active account info.
        """
        del ctx
        account = _ma.get_account(account_id)
        if not account:
            return {"success": False, "message": f"Account {account_id} not found"}
        if _ma.set_default_account(account_id):
            _ma.update_account_last_used(account_id)
            return {
                "success": True,
                "active_account_id": account_id,
                "name": account.name,
                "username": account.username,
                "identity_type": account.identity_type,
                "message": f"Switched to account {account.name}",
            }
        return {"success": False, "message": f"Failed to switch to account {account.name}"}

    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="Get Active Account",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"account", "management"},
    )
    @tool_guard("get_active_account_info")
    async def get_active_account_info(ctx: Context = CurrentContext()) -> dict[str, Any]:
        """
        Get information about the currently active Facebook identity.

        Returns:
            Active account metadata or a message when none is set.
        """
        del ctx
        account = _ma.get_active_account()
        if not account:
            return {
                "active_account": None,
                "message": "No stored account set; using default profile",
            }
        return {
            "active_account": {
                "account_id": account.account_id,
                "name": account.name,
                "username": account.username,
                "identity_type": account.identity_type,
                "is_active": account.is_active,
                "last_used": account.last_used,
            },
        }

    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="Update Account Cookies",
        annotations={"destructiveHint": True, "openWorldHint": True},
        tags={"account", "management"},
    )
    @tool_guard("update_account_cookies")
    async def update_account_cookies(
        account_id: Annotated[str, Field(min_length=1, max_length=64)],
        cookies: dict[str, str],
        ctx: Context = CurrentContext(),
    ) -> dict[str, Any]:
        """
        Update cookies for an existing stored Facebook identity.

        Useful for refreshing an expired session without re-adding the account.

        Args:
            account_id: The account id to update
            cookies: New cookie dict (c_user + xs)

        Returns:
            Success status.
        """
        del ctx
        account = _ma.get_account(account_id)
        if not account:
            return {"success": False, "message": f"Account {account_id} not found"}
        if _ma.set_account_cookies(account_id, cookies):
            return {"success": True, "message": f"Cookies updated for account {account.name}"}
        return {"success": False, "message": f"Failed to update cookies for account {account.name}"}

    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="Import Account from Browser",
        annotations={"destructiveHint": True, "openWorldHint": True},
        tags={"account", "management"},
    )
    @tool_guard("import_account_from_browser")
    async def import_account_from_browser(
        account_id: Annotated[str, Field(min_length=1, max_length=64)],
        name: Annotated[str, Field(min_length=1, max_length=255)],
        browser: Annotated[str | None, Field(max_length=32)] = None,
        username: Annotated[str | None, Field(max_length=255)] = None,
        identity_type: Annotated[str | None, Field(max_length=16)] = "user",
        set_as_active: bool = True,
        ctx: Context = CurrentContext(),
    ) -> dict[str, Any]:
        """
        Import a Facebook identity's cookies directly from a browser.

        Uses the native cookie import machinery (the same as `--login`). The
        browser must be logged into that identity. Example: a Page whose
        session is open in your browser profile.

        Args:
            account_id: The Facebook account/page id (numeric)
            name: Display name for the identity
            browser: Browser to import from (chrome, brave, edge, firefox, zen, ...)
            username: Optional vanity username
            identity_type: user, page, or profile
            set_as_active: Make this the active identity (default True)

        Returns:
            Success status and cookie names found.
        """
        from facebook_mcp_server.cookie_import import extract_cookies_from_browser

        del ctx
        try:
            cookies = extract_cookies_from_browser(browser_id=browser)
        except Exception as e:
            return {
                "success": False,
                "message": f"Could not import cookies from browser: {e}",
            }

        if not cookies:
            return {
                "success": False,
                "message": (
                    "No Facebook cookies found in that browser. "
                    "Log into facebook.com there first, or pass --browser."
                ),
            }

        missing = {"c_user", "xs"} - set(cookies.keys())
        if missing:
            return {
                "success": False,
                "message": f"Browser session is missing required cookies: {sorted(missing)}",
            }

        _ma.create_account(
            account_id=account_id,
            cookies=cookies,
            name=name,
            username=username or "",
            identity_type=identity_type or "user",
        )
        if set_as_active:
            _ma.set_default_account(account_id)

        return {
            "success": True,
            "account_id": account_id,
            "name": name,
            "cookies_found": sorted(cookies.keys()),
            "message": f"Account {name} imported from {browser or 'default browser'}",
        }

    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="Get Account Posting Status",
        annotations={"readOnlyHint": True},
        tags={"account", "posting"},
    )
    @tool_guard("get_account_posting_status")
    async def get_account_posting_status(
        account_id: Annotated[str | None, Field(max_length=64)] = None,
        ctx: Context = CurrentContext(),
    ) -> dict[str, Any]:
        """
        Check a stored identity's posting limits and history.

        Args:
            account_id: Account id (defaults to the active account)

        Returns:
            Dict with can_post, reason, post_count_today, daily_post_limit,
            and recent history.
        """
        del ctx
        target_id = account_id
        if not target_id:
            active = _ma.get_active_account()
            if not active:
                return {"can_post": False, "reason": "No account configured"}
            target_id = active.account_id

        can_post, reason = _ma.check_account_posting_limits(target_id)
        account = _ma.get_account(target_id)
        history = _ma.get_account_posting_history(target_id, limit=10)
        return {
            "account_id": target_id,
            "can_post": can_post,
            "reason": reason,
            "post_count_today": account.post_count_today if account else 0,
            "daily_post_limit": account.daily_post_limit if account else 0,
            "posting_enabled": account.posting_enabled if account else False,
            "recent_attempts": history,
        }
