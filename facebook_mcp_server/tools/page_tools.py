"""
Page-level tools: profile/page details, timeline posts, and page search via
cookie-based scraping.
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


def register_page_tools(mcp: FastMCP) -> None:
    """Register all page-related tools with the MCP server."""

    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="Get Page Info",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"page", "scraping"},
    )
    @tool_guard("get_page_info")
    async def get_page_info(
        username: Annotated[str, Field(min_length=1, max_length=255)],
        ctx: Context = CurrentContext(),
    ) -> dict[str, Any]:
        """
        Get Facebook Page/profile details by username.

        Fetches the public (or logged-in) profile page and parses name, id,
        bio, and follower counts from the embedded page data.

        Args:
            username: Page or profile username (e.g. 'cocacola')

        Returns:
            Dict with username, url, id, name, bio, and follower counts.
        """
        client = await get_ready_client(ctx, tool_name="get_page_info")
        return await client.get_profile(username)

    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="Get Page Posts",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"page", "posts", "scraping"},
    )
    @tool_guard("get_page_posts")
    async def get_page_posts(
        username: Annotated[str, Field(min_length=1, max_length=255)],
        max_posts: Annotated[int, Field(ge=1, le=100)] = 25,
        ctx: Context = CurrentContext(),
    ) -> dict[str, Any]:
        """
        Get recent posts from a Facebook Page's timeline.

        Args:
            username: Page username (e.g. 'cocacola')
            max_posts: Maximum posts to return (1-100, default 25)

        Returns:
            Dict with posts (id, url, text, created_time) and total.
        """
        client = await get_ready_client(ctx, tool_name="get_page_posts")
        return await client.get_profile_posts(username, limit=max_posts)

    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="Search Pages",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"page", "search", "scraping"},
    )
    @tool_guard("search_pages")
    async def search_pages(
        query: Annotated[str, Field(min_length=1, max_length=200)],
        limit: Annotated[int, Field(ge=1, le=50)] = 25,
        ctx: Context = CurrentContext(),
    ) -> dict[str, Any]:
        """
        Search for public Facebook Pages by name.

        Args:
            query: Search term
            limit: Maximum results to return (1-50, default 25)

        Returns:
            Dict with results (id, name, username, url) and total.
        """
        client = await get_ready_client(ctx, tool_name="search_pages")
        return await client.search_pages(query, limit=limit)
