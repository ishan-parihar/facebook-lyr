"""
FastMCP server implementation for Facebook / Messenger integration with tool
registration.

Creates and configures the MCP server with the full tool suite: accounts,
multi-account management, pages, posts, media, comments, Messenger, groups,
and session management.
"""

import logging
from collections.abc import AsyncIterator
from typing import Any

from fastmcp import Context, FastMCP
from fastmcp.dependencies import CurrentContext
from fastmcp.server.lifespan import lifespan

from facebook_mcp_server.bootstrap import initialize_bootstrap
from facebook_mcp_server.constants import TOOL_TIMEOUT_SECONDS
from facebook_mcp_server.error_handler import raise_tool_error
from facebook_mcp_server.sequential_tool_middleware import (
    SequentialToolExecutionMiddleware,
)
from facebook_mcp_server.tools import (
    register_account_tools,
    register_comment_tools,
    register_discovery_tools,
    register_group_tools,
    register_messenger_tools,
    register_multi_account_tools,
    register_page_tools,
    register_posting_tools,
    register_post_tools,
)

logger = logging.getLogger(__name__)


@lifespan
async def api_lifespan(app: FastMCP) -> AsyncIterator[dict[str, Any]]:
    """Manage API lifecycle — no browser, no setup downloads.

    All requests go through Facebook's private-web surface using imported
    browser cookies (c_user + xs). Authentication is loaded on demand per
    tool call.
    """
    del app
    logger.info("Facebook MCP Server starting...")
    initialize_bootstrap()
    logger.info("Cookie-scraping mode: no browser setup needed")

    yield {}

    logger.info("Facebook MCP Server shutting down...")


def create_mcp_server() -> FastMCP:
    """Create and configure the MCP server with all Facebook tools."""
    mcp = FastMCP(
        "facebook_graph",
        lifespan=api_lifespan,
        mask_error_details=True,
    )
    mcp.add_middleware(SequentialToolExecutionMiddleware())

    # Register all tools
    register_account_tools(mcp)
    register_multi_account_tools(mcp)
    register_page_tools(mcp)
    register_post_tools(mcp)
    register_posting_tools(mcp)
    register_comment_tools(mcp)
    register_messenger_tools(mcp)
    register_group_tools(mcp)
    register_discovery_tools(mcp)

    # Register session management tool
    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="Close Session",
        annotations={"destructiveHint": True},
        tags={"session"},
    )
    async def close_session(ctx: Context = CurrentContext()) -> dict[str, Any]:
        """Close all active API clients and clean up resources."""
        try:
            from facebook_mcp_server.dependencies import close_all_clients

            closed = await close_all_clients()
            return {
                "status": "success",
                "closed_clients": closed,
                "message": (
                    "Closed active Facebook scrape clients. No browser resources "
                    "to clean up (cookie-scraping mode)."
                ),
            }
        except Exception as e:
            raise_tool_error(e, "close_session")  # NoReturn

    return mcp
