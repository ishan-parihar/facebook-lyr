"""
Account-level tools: cookie-session identity, session status, and active-Page
selection.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastmcp import Context, FastMCP
from fastmcp.dependencies import CurrentContext
from pydantic import Field

from facebook_mcp_server.constants import TOOL_TIMEOUT_SECONDS
from facebook_mcp_server.dependencies import get_ready_client
from facebook_mcp_server.session_state import (
    load_active_page,
    save_active_page,
)
from facebook_mcp_server.tools._guard import tool_guard

logger = logging.getLogger(__name__)


def register_account_tools(mcp: FastMCP) -> None:
    """Register all account-related tools with the MCP server."""

    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="Get Me",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"account", "scraping"},
    )
    @tool_guard("get_me")
    async def get_me(ctx: Context = CurrentContext()) -> dict[str, Any]:
        """
        Get the identity of the authenticated Facebook user.

        Resolved from the imported browser-cookie session (c_user / xs). Useful
        to confirm whose session is configured and whether it is still valid.

        Returns:
            Dict with the user's id, name, and username.
        """
        client = await get_ready_client(ctx, tool_name="get_me")
        me = await client.get_me()
        return {"me": me}

    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="Get Account Status",
        annotations={"readOnlyHint": True},
        tags={"account", "status"},
    )
    @tool_guard("get_account_status")
    async def get_account_status(ctx: Context = CurrentContext()) -> dict[str, Any]:
        """
        Check the current cookie-session status and active Page.

        Returns:
            Dict with valid (bool), cookie_source, me (id/name), and
            active_page_id / active_page_name.
        """
        client = await get_ready_client(ctx, tool_name="get_account_status")
        valid = await client.validate_session()
        me = await client.get_me() if valid else {}
        active_page = load_active_page()
        return {
            "valid": valid,
            "cookie_source": "profile",
            "me": me,
            "active_page_id": active_page.get("page_id"),
            "active_page_name": active_page.get("page_name"),
        }

    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="Set Active Page",
        annotations={"destructiveHint": True},
        tags={"account", "config"},
    )
    @tool_guard("set_active_page")
    async def set_active_page(
        page_id: str,
        page_name: Annotated[str | None, Field(max_length=255)] = None,
        ctx: Context = CurrentContext(),
    ) -> dict[str, Any]:
        """
        Persist the active Facebook Page for display/context purposes.

        Note: cookie scraping operates as the account whose cookies are
        imported (c_user). This tool records a Page for agent bookkeeping and
        for Page-mode sessions (m_page_voice cookie present). It does not
        switch the acting identity on its own.

        Args:
            page_id: The Facebook Page id (numeric string)
            page_name: Optional human-readable Page name for display

        Returns:
            Dict with status and the saved active_page.
        """
        del ctx
        saved = save_active_page(page_id, page_name)
        return {
            "status": "ok",
            "active_page_id": page_id,
            "active_page_name": page_name,
            "message": (
                "Active Page recorded. Note: cookie scraping acts as the "
                "account whose cookies are imported."
            ),
            "saved": saved,
        }
