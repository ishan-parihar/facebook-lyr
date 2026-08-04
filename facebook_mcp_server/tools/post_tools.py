"""
Post-level read tools: user timeline posts, post details, and post search via
cookie-based scraping. Write tools (create_post / photo / video / carousel)
live in ``tools/posting_tools.py``.
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


def register_post_tools(mcp: FastMCP) -> None:
    """Register all post-related tools with the MCP server."""

    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="Get User Posts",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"posts", "scraping"},
    )
    @tool_guard("get_user_posts")
    async def get_user_posts(
        username: Annotated[str, Field(min_length=1, max_length=255)],
        max_posts: Annotated[int, Field(ge=1, le=100)] = 25,
        ctx: Context = CurrentContext(),
    ) -> dict[str, Any]:
        """
        Get recent posts from a user's or Page's timeline.

        Args:
            username: Username (e.g. 'natgeo')
            max_posts: Maximum posts to return (1-100, default 25)

        Returns:
            Dict with posts (id, url, text, created_time) and total.
        """
        client = await get_ready_client(ctx, tool_name="get_user_posts")
        return await client.get_profile_posts(username, limit=max_posts)

    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="Get Post Details",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"posts", "scraping"},
    )
    @tool_guard("get_post_details")
    async def get_post_details(
        post_url: Annotated[str, Field(min_length=1, max_length=1000)],
        ctx: Context = CurrentContext(),
    ) -> dict[str, Any]:
        """
        Get details for a single post by its URL.

        Args:
            post_url: Full post URL (e.g. https://www.facebook.com/user/posts/123...)

        Returns:
            Dict with url and post (id, url, text, created_time).
        """
        client = await get_ready_client(ctx, tool_name="get_post_details")
        return await client.get_post(post_url)

    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="Search Posts",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"posts", "search", "scraping"},
    )
    @tool_guard("search_posts")
    async def search_posts(
        query: Annotated[str, Field(min_length=1, max_length=200)],
        limit: Annotated[int, Field(ge=1, le=50)] = 25,
        ctx: Context = CurrentContext(),
    ) -> dict[str, Any]:
        """
        Search public posts by keyword.

        Args:
            query: Search term
            limit: Maximum results to return (1-50, default 25)

        Returns:
            Dict with results and total.
        """
        client = await get_ready_client(ctx, tool_name="search_posts")
        return await client.search_posts(query, limit=limit)
