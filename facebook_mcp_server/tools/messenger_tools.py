"""
Messenger tools: conversations, messages, sending, typing, and seen markers
via Facebook's legacy Mercury ajax endpoints (cookie-authenticated).
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


def register_messenger_tools(mcp: FastMCP) -> None:
    """Register all Messenger-related tools with the MCP server."""

    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="Search People",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"messenger", "search", "scraping"},
    )
    @tool_guard("search_people")
    async def search_people(
        query: Annotated[str, Field(min_length=1, max_length=200)],
        limit: Annotated[int, Field(ge=1, le=50)] = 25,
        ctx: Context = CurrentContext(),
    ) -> dict[str, Any]:
        """
        Search for people by name.

        Useful to find a person before messaging them (pair with
        get_conversations / send_message).

        Args:
            query: Search term (name)
            limit: Maximum results to return (1-50, default 25)

        Returns:
            Dict with results (id, name, username, url) and total.
        """
        client = await get_ready_client(ctx, tool_name="search_people")
        return await client.search_people(query, limit=limit)

    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="Get Conversations",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"messenger", "scraping"},
    )
    @tool_guard("get_conversations")
    async def get_conversations(
        limit: Annotated[int, Field(ge=1, le=100)] = 25,
        ctx: Context = CurrentContext(),
    ) -> dict[str, Any]:
        """
        List Messenger conversations for the authenticated account.

        Args:
            limit: Maximum conversations to return (1-100, default 25)

        Returns:
            Dict with conversations (thread_id, name, snippet, timestamp,
            unread_count) and total.
        """
        client = await get_ready_client(ctx, tool_name="get_conversations")
        return await client.get_thread_list(limit=limit)

    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="Get Conversation Messages",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"messenger", "scraping"},
    )
    @tool_guard("get_conversation_messages")
    async def get_conversation_messages(
        thread_id: Annotated[str, Field(min_length=1, max_length=255)],
        ctx: Context = CurrentContext(),
    ) -> dict[str, Any]:
        """
        Read messages from a Messenger conversation.

        Args:
            thread_id: Conversation id (from get_conversations)

        Returns:
            Dict with thread_id, messages (message_id, sender, body,
            timestamp), total.
        """
        client = await get_ready_client(ctx, tool_name="get_conversation_messages")
        return await client.get_thread(thread_id)

    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="Send Message",
        annotations={"destructiveHint": True, "writeHint": True},
        tags={"messenger", "write"},
    )
    @tool_guard("send_message")
    async def send_message(
        thread_id: Annotated[str, Field(min_length=1, max_length=255)],
        message: Annotated[str, Field(min_length=1, max_length=4000)],
        confirm: Annotated[bool, Field(description="Must be true to send")] = False,
        account_id: Annotated[str | None, Field(max_length=64)] = None,
        ctx: Context = CurrentContext(),
    ) -> dict[str, Any]:
        """
        Send a Messenger text message as the active (or specified) identity.

        Validates the text, checks the identity's posting limits, records the
        attempt, and sends via the legacy Mercury endpoint. Fails honestly if
        sending is decommissioned for the account.

        Args:
            thread_id: Conversation id (from get_conversations)
            message: Message text
            confirm: Must be set to true to actually send (safety gate)
            account_id: Optional stored identity id to send as (default: active)

        Returns:
            Dict with success, message_id, and thread_id.
        """
        require_confirm(
            confirm,
            "send_message requires confirm=true to send. This sends a "
            "message to a real person on your behalf.",
        )
        posting = await get_ready_posting_client(
            ctx, tool_name="send_message", account_id=account_id
        )
        return await posting.send_message(thread_id, message)

    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="Delete Message",
        annotations={"destructiveHint": True, "idempotentHint": False, "writeHint": True},
        tags={"messenger", "write"},
    )
    @tool_guard("delete_message")
    async def delete_message(
        thread_id: Annotated[str, Field(min_length=1, max_length=255)],
        message_id: Annotated[str, Field(min_length=1, max_length=255)],
        confirm: Annotated[bool, Field(description="Must be true to delete")] = False,
        account_id: Annotated[str | None, Field(max_length=64)] = None,
        ctx: Context = CurrentContext(),
    ) -> dict[str, Any]:
        """
        Delete (unsend) a Messenger message as the active (or specified) identity.

        Validates the ids, checks the identity's posting limits, records the
        attempt, and deletes via the legacy Mercury endpoint. Fails honestly
        if deletion is decommissioned for the account.

        Args:
            thread_id: Conversation id
            message_id: The message id to delete (from get_conversation_messages)
            confirm: Must be set to true to actually delete (safety gate)
            account_id: Optional stored identity id (default: active)

        Returns:
            Dict with success, thread_id, and message_id.
        """
        require_confirm(
            confirm,
            "delete_message requires confirm=true to delete. This removes the "
            "message for everyone in the conversation.",
        )
        posting = await get_ready_posting_client(
            ctx, tool_name="delete_message", account_id=account_id
        )
        return await posting.delete_message(thread_id, message_id)

    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="Mark Conversation Seen",
        annotations={"writeHint": True},
        tags={"messenger", "write"},
    )
    @tool_guard("mark_conversation_seen")
    async def mark_conversation_seen(
        thread_id: Annotated[str, Field(min_length=1, max_length=255)],
        ctx: Context = CurrentContext(),
    ) -> dict[str, Any]:
        """
        Mark a Messenger conversation as seen (clears the unread badge).

        Args:
            thread_id: Conversation id

        Returns:
            Dict with thread_id and success.
        """
        client = await get_ready_client(ctx, tool_name="mark_conversation_seen")
        return await client.mark_seen(thread_id)

    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="Send Typing Indicator",
        annotations={"writeHint": True},
        tags={"messenger", "write"},
    )
    @tool_guard("send_typing_indicator")
    async def send_typing_indicator(
        thread_id: Annotated[str, Field(min_length=1, max_length=255)],
        typing: Annotated[bool, Field(description="true = typing_on, false = typing_off")] = True,
        ctx: Context = CurrentContext(),
    ) -> dict[str, Any]:
        """
        Send a typing indicator to a conversation (typing_on / typing_off).

        Args:
            thread_id: Conversation id
            typing: True to show typing, False to stop

        Returns:
            Dict with thread_id, typing, and success.
        """
        client = await get_ready_client(ctx, tool_name="send_typing_indicator")
        return await client.send_typing(thread_id, typing=typing)
