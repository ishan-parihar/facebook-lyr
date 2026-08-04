"""
Comment-level tools: reading and writing post comments via cookie-based
scraping (ufi endpoints).
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastmcp import Context, FastMCP
from fastmcp.dependencies import CurrentContext
from pydantic import Field

from facebook_mcp_server.constants import TOOL_TIMEOUT_SECONDS
from facebook_mcp_server.dependencies import get_ready_client, get_ready_posting_client
from facebook_mcp_server.exceptions import require_confirm
from facebook_mcp_server.tools._guard import tool_guard

logger = logging.getLogger(__name__)


def register_comment_tools(mcp: FastMCP) -> None:
    """Register all comment-related tools with the MCP server."""

    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="Get Post Comments",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"comments", "scraping"},
    )
    @tool_guard("get_post_comments")
    async def get_post_comments(
        post_id: Annotated[str, Field(min_length=1, max_length=255)],
        limit: Annotated[int, Field(ge=1, le=100)] = 25,
        ctx: Context = CurrentContext(),
    ) -> dict[str, Any]:
        """
        Fetch comments on a post.

        Args:
            post_id: Numeric post id (from get_page_posts/get_user_posts)
            limit: Maximum comments to return (1-100, default 25)

        Returns:
            Dict with post_id, comments (id, author, text, timestamp), total.
        """
        client = await get_ready_client(ctx, tool_name="get_post_comments")
        return await client.get_post_comments(post_id, limit=limit)

    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="Comment On Post",
        annotations={"destructiveHint": True, "writeHint": True},
        tags={"comments", "write"},
    )
    @tool_guard("comment_on_post")
    async def comment_on_post(
        post_id: Annotated[str, Field(min_length=1, max_length=255)],
        text: Annotated[str, Field(min_length=1, max_length=2000)],
        confirm: Annotated[bool, Field(description="Must be true to publish")] = False,
        account_id: Annotated[str | None, Field(max_length=64)] = None,
        ctx: Context = CurrentContext(),
    ) -> dict[str, Any]:
        """
        Comment on a post as the active (or specified) Facebook identity.

        Validates the text, checks the identity's posting limits, records the
        attempt, and comments through the web composer. Fails honestly if
        cookie-based commenting is decommissioned for the account.

        Args:
            post_id: Numeric post id
            text: Comment text
            confirm: Must be set to true to actually comment (safety gate)
            account_id: Optional stored identity id to comment as (default: active)

        Returns:
            Dict with success and comment_id.
        """
        require_confirm(
            confirm,
            "comment_on_post requires confirm=true to publish. This posts publicly on your behalf.",
        )
        posting = await get_ready_posting_client(
            ctx, tool_name="comment_on_post", account_id=account_id
        )
        return await posting.comment_on_post(post_id, text)
