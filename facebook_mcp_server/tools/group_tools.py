"""
Group-level tools: group info, group feed, and group search via cookie-based
scraping.
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


def register_group_tools(mcp: FastMCP) -> None:
    """Register all group-related tools with the MCP server."""

    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="Get Group",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"groups", "scraping"},
    )
    @tool_guard("get_group")
    async def get_group(
        group_id: Annotated[str, Field(min_length=1, max_length=255)],
        ctx: Context = CurrentContext(),
    ) -> dict[str, Any]:
        """
        Get Facebook group details.

        Args:
            group_id: Numeric group id (from search_groups)

        Returns:
            Dict with id, url, name, and cover photo.
        """
        client = await get_ready_client(ctx, tool_name="get_group")
        return await client.get_group(group_id)

    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="Get Group Posts",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"groups", "posts", "scraping"},
    )
    @tool_guard("get_group_posts")
    async def get_group_posts(
        group_id: Annotated[str, Field(min_length=1, max_length=255)],
        max_posts: Annotated[int, Field(ge=1, le=100)] = 25,
        ctx: Context = CurrentContext(),
    ) -> dict[str, Any]:
        """
        Get recent posts from a group feed.

        Args:
            group_id: Numeric group id
            max_posts: Maximum posts to return (1-100, default 25)

        Returns:
            Dict with group_id, posts, and total.
        """
        client = await get_ready_client(ctx, tool_name="get_group_posts")
        return await client.get_group_posts(group_id, limit=max_posts)

    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="Get Group Members",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"groups", "scraping"},
    )
    @tool_guard("get_group_members")
    async def get_group_members(
        group_id: Annotated[str, Field(min_length=1, max_length=255)],
        limit: Annotated[int, Field(ge=1, le=100)] = 25,
        ctx: Context = CurrentContext(),
    ) -> dict[str, Any]:
        """
        List members of a Facebook group.

        Best-effort: served from the legacy browser-list endpoint, which may
        return an error for private groups or migrated surfaces — the result
        then explains the limitation honestly.

        Args:
            group_id: Numeric group id
            limit: Maximum members to return (1-100, default 25)

        Returns:
            Dict with group_id, members (id, name, url, role), and total.
        """
        client = await get_ready_client(ctx, tool_name="get_group_members")
        return await client.get_group_members(group_id, limit=limit)

    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="Search Groups",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"groups", "search", "scraping"},
    )
    @tool_guard("search_groups")
    async def search_groups(
        query: Annotated[str, Field(min_length=1, max_length=200)],
        limit: Annotated[int, Field(ge=1, le=50)] = 25,
        ctx: Context = CurrentContext(),
    ) -> dict[str, Any]:
        """
        Search for Facebook groups by name.

        Args:
            query: Search term
            limit: Maximum results to return (1-50, default 25)

        Returns:
            Dict with results and total.
        """
        client = await get_ready_client(ctx, tool_name="search_groups")
        return await client.search_groups(query, limit=limit)
