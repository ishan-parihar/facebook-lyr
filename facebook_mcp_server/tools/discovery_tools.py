"""
Discovery tools: notifications and events via cookie-based scraping.

These give an AI agent ambient awareness of the identity's account state —
new notifications (messages, tags, follows) and upcoming events — so it can
proactively act instead of only reacting to explicit queries.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastmcp import Context, FastMCP
from fastmcp.dependencies import CurrentContext
from pydantic import Field

from facebook_mcp_server.constants import TOOL_TIMEOUT_SECONDS
from facebook_mcp_server.dependencies import get_ready_client
from facebook_mcp_server.tools._guard import tool_guard

logger = logging.getLogger(__name__)


def register_discovery_tools(mcp: FastMCP) -> None:
    """Register notifications/events discovery tools with the MCP server."""

    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="Get Notifications",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"notifications", "scraping"},
    )
    @tool_guard("get_notifications")
    async def get_notifications(
        limit: Annotated[int, Field(ge=1, le=100)] = 25,
        ctx: Context = CurrentContext(),
    ) -> dict[str, Any]:
        """
        List the authenticated identity's recent Facebook notifications.

        Includes message alerts, tags, follows, and page activity. Useful for
        an agent to know what needs attention before deciding to act.

        Args:
            limit: Maximum notifications to return (1-100, default 25)

        Returns:
            Dict with notifications (id, text, type, url, unread, timestamp)
            and total.
        """
        client = await get_ready_client(ctx, tool_name="get_notifications")
        return await client.get_notifications(limit=limit)

    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="Get Events",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"events", "scraping"},
    )
    @tool_guard("get_events")
    async def get_events(
        limit: Annotated[int, Field(ge=1, le=100)] = 25,
        ctx: Context = CurrentContext(),
    ) -> dict[str, Any]:
        """
        List upcoming events relevant to the authenticated identity.

        Returns embedded event rows (id, name, start_date_time, url) from the
        events hub. Event details that load client-side are not available via
        cookie scraping.

        Args:
            limit: Maximum events to return (1-100, default 25)

        Returns:
            Dict with events (id, name, start_date_time, url) and total.
        """
        client = await get_ready_client(ctx, tool_name="get_events")
        return await client.get_events(limit=limit)
