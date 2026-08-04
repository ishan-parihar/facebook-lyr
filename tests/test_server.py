import asyncio
from unittest.mock import AsyncMock, MagicMock, call

import mcp.types as mt
from fastmcp import FastMCP
from fastmcp.server.middleware import MiddlewareContext

from facebook_mcp_server.sequential_tool_middleware import (
    SequentialToolExecutionMiddleware,
)
from facebook_mcp_server.server import create_mcp_server


class TestSequentialToolExecutionMiddleware:
    async def test_create_mcp_server_registers_middleware(self):
        mcp = create_mcp_server()

        assert any(
            isinstance(middleware, SequentialToolExecutionMiddleware)
            for middleware in mcp.middleware
        )

    async def test_serializes_parallel_tool_calls(self):
        mcp = FastMCP("test")
        mcp.add_middleware(SequentialToolExecutionMiddleware())

        active_calls = 0
        max_active_calls = 0

        @mcp.tool
        async def slow_tool(delay: float = 0.05) -> dict[str, float]:
            nonlocal active_calls, max_active_calls
            active_calls += 1
            max_active_calls = max(max_active_calls, active_calls)
            try:
                await asyncio.sleep(delay)
                return {"delay": delay}
            finally:
                active_calls -= 1

        result_one, result_two = await asyncio.gather(
            mcp.call_tool("slow_tool", {"delay": 0.05}),
            mcp.call_tool("slow_tool", {"delay": 0.05}),
        )

        assert max_active_calls == 1
        assert result_one.structured_content == {"delay": 0.05}
        assert result_two.structured_content == {"delay": 0.05}

    async def test_reports_queue_progress(self):
        middleware = SequentialToolExecutionMiddleware()
        fastmcp_context = MagicMock()
        fastmcp_context.request_context = object()
        fastmcp_context.report_progress = AsyncMock()
        call_next = AsyncMock(return_value=MagicMock())
        context = MiddlewareContext(
            message=mt.CallToolRequestParams(name="get_me", arguments={}),
            method="tools/call",
            fastmcp_context=fastmcp_context,
        )

        await middleware.on_call_tool(context, call_next)

        fastmcp_context.report_progress.assert_has_awaits(
            [
                call(
                    progress=0,
                    total=100,
                    message="Queued waiting for API lock",
                ),
                call(
                    progress=0,
                    total=100,
                    message="API lock acquired, starting tool",
                ),
            ]
        )


class TestToolRegistration:
    async def test_expected_tools_registered(self):
        mcp = create_mcp_server()
        tools = await mcp.list_tools()
        names = {t.name for t in tools}

        for expected in (
            "get_me",
            "get_account_status",
            "set_active_page",
            "get_page_info",
            "get_page_posts",
            "search_pages",
            "get_user_posts",
            "get_post_details",
            "create_post",
            "search_posts",
            "create_photo_post",
            "create_video_post",
            "create_carousel_post",
            "get_post_comments",
            "comment_on_post",
            "search_people",
            "get_conversations",
            "get_conversation_messages",
            "send_message",
            "send_typing_indicator",
            "mark_conversation_seen",
            "get_group",
            "get_group_posts",
            "search_groups",
            "close_session",
            # multi-account suite
            "list_accounts",
            "list_switchable_accounts",
            "add_account",
            "remove_account",
            "switch_active_account",
            "get_active_account_info",
            "update_account_cookies",
            "import_account_from_browser",
            "get_account_posting_status",
        ):
            assert expected in names, f"Missing tool: {expected}"

        # Obsolete Graph-API tools must be gone.
        for gone in (
            "list_pages",
            "create_page_post",
            "publish_thread",
            "create_thread_container",
            "get_page_insights",
            "send_attachment_message",
            "get_threads_profile",
            "get_user_threads",
        ):
            assert gone not in names, f"Stale tool still present: {gone}"
