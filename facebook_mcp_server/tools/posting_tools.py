"""
Write tools: text, photo, video, and carousel publishing via the PostingClient.

Mirrors instagram-lyr's ``tools/posting_tools.py`` — all write operations live
in one module and route through the posting layer (validation, media
processing, per-identity posting limits, attempt recording). Every write is
confirm-gated and attempted through the cookie-authenticated web composer.
When the transport is decommissioned for the account (modern Facebook migrated
writes to private GraphQL with rotating doc_ids), the tool surfaces a clean,
honest error instead of pretending the post went through.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastmcp import Context, FastMCP
from fastmcp.dependencies import CurrentContext
from pydantic import Field

from facebook_mcp_server.constants import TOOL_TIMEOUT_SECONDS
from facebook_mcp_server.dependencies import get_ready_posting_client
from facebook_mcp_server.exceptions import require_confirm
from facebook_mcp_server.tools._guard import tool_guard

logger = logging.getLogger(__name__)


def register_posting_tools(mcp: FastMCP) -> None:
    """Register all write tools with the MCP server."""

    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="Create Post",
        annotations={"destructiveHint": True, "idempotentHint": False, "writeHint": True},
        tags={"posts", "write"},
    )
    @tool_guard("create_post")
    async def create_post(
        status: Annotated[str, Field(min_length=1, max_length=5000)],
        confirm: Annotated[bool, Field(description="Must be true to publish")] = False,
        account_id: Annotated[str | None, Field(max_length=64)] = None,
        ctx: Context = CurrentContext(),
    ) -> dict[str, Any]:
        """
        Publish a text post as the active (or specified) Facebook identity.

        Validates the text, checks the identity's posting limits, and publishes
        through the web composer. If Facebook has decommissioned cookie-based
        writing for the account, returns a clear error rather than a false
        success.

        Args:
            status: The post text (max 5000 chars)
            confirm: Must be set to true to actually publish (safety gate)
            account_id: Optional stored identity id to post as (default: active)

        Returns:
            Dict with success and post_id.
        """
        require_confirm(
            confirm,
            "create_post requires confirm=true to publish. This is a "
            "public write action on your Facebook timeline.",
        )
        posting = await get_ready_posting_client(
            ctx, tool_name="create_post", account_id=account_id
        )
        return await posting.create_text_post(status)

    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="Create Photo Post",
        annotations={"destructiveHint": True, "idempotentHint": False, "writeHint": True},
        tags={"media", "write"},
    )
    @tool_guard("create_photo_post")
    async def create_photo_post(
        source: Annotated[
            str,
            Field(description="Local file path or http(s) URL of the image to post"),
        ],
        message: Annotated[str, Field(max_length=5000)] = "",
        confirm: Annotated[bool, Field(description="Must be true to publish")] = False,
        account_id: Annotated[str | None, Field(max_length=64)] = None,
        ctx: Context = CurrentContext(),
    ) -> dict[str, Any]:
        """
        Upload a photo and publish it as the active (or specified) identity.

        The image is validated and normalized (resized to Facebook's feed
        spec) before the upload attempt. Best-effort: modern Facebook may
        reject cookie-based uploads; failures surface as clear errors.

        Args:
            source: Local file path or http(s) URL of the image
            message: Optional caption/text with the photo
            confirm: Must be set to true to actually publish (safety gate)
            account_id: Optional stored identity id to post as (default: active)

        Returns:
            Dict with success, post_id, and attachment_id.
        """
        require_confirm(
            confirm,
            "create_photo_post requires confirm=true to publish. This is a "
            "public write action on your Facebook timeline.",
        )
        posting = await get_ready_posting_client(
            ctx, tool_name="create_photo_post", account_id=account_id
        )
        return await posting.create_photo_post(source, message=message)

    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="Create Video Post",
        annotations={"destructiveHint": True, "idempotentHint": False, "writeHint": True},
        tags={"media", "video", "write"},
    )
    @tool_guard("create_video_post")
    async def create_video_post(
        source: Annotated[
            str,
            Field(description="Local file path or http(s) URL of the video to post"),
        ],
        message: Annotated[str, Field(max_length=5000)] = "",
        confirm: Annotated[bool, Field(description="Must be true to publish")] = False,
        account_id: Annotated[str | None, Field(max_length=64)] = None,
        ctx: Context = CurrentContext(),
    ) -> dict[str, Any]:
        """
        Upload a video and publish it as the active (or specified) identity.

        The video file is validated before the upload attempt. Best-effort:
        modern Facebook has largely decommissioned cookie-based video uploads;
        failures surface as clear, honest errors.

        Args:
            source: Local file path or http(s) URL of the video (mp4, mov, ...)
            message: Optional caption/text with the video
            confirm: Must be set to true to actually publish (safety gate)
            account_id: Optional stored identity id to post as (default: active)

        Returns:
            Dict with success, post_id, and attachment_id.
        """
        require_confirm(
            confirm,
            "create_video_post requires confirm=true to publish. This is a "
            "public write action on your Facebook timeline.",
        )
        posting = await get_ready_posting_client(
            ctx, tool_name="create_video_post", account_id=account_id
        )
        return await posting.create_video_post(source, message=message)

    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="Create Carousel Post",
        annotations={"destructiveHint": True, "idempotentHint": False, "writeHint": True},
        tags={"media", "carousel", "write"},
    )
    @tool_guard("create_carousel_post")
    async def create_carousel_post(
        sources: Annotated[
            list[str],
            Field(
                description=(
                    "List of local file paths or http(s) URLs (2-10 images) for the carousel"
                )
            ),
        ],
        message: Annotated[str, Field(max_length=5000)] = "",
        confirm: Annotated[bool, Field(description="Must be true to publish")] = False,
        account_id: Annotated[str | None, Field(max_length=64)] = None,
        ctx: Context = CurrentContext(),
    ) -> dict[str, Any]:
        """
        Upload multiple photos and publish them as one carousel post.

        Each image is validated and normalized before the upload attempt.
        Best-effort: modern Facebook may reject cookie-based carousels;
        failures surface as clear, honest errors.

        Args:
            sources: 2-10 local file paths or http(s) URLs of images
            message: Optional caption/text with the carousel
            confirm: Must be set to true to actually publish (safety gate)
            account_id: Optional stored identity id to post as (default: active)

        Returns:
            Dict with success, post_id, and attachment_ids.
        """
        require_confirm(
            confirm,
            "create_carousel_post requires confirm=true to publish. This is a "
            "public write action on your Facebook timeline.",
        )
        posting = await get_ready_posting_client(
            ctx, tool_name="create_carousel_post", account_id=account_id
        )
        return await posting.create_carousel_post(sources, message=message)

    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="Delete Post",
        annotations={"destructiveHint": True, "idempotentHint": False, "writeHint": True},
        tags={"posts", "write"},
    )
    @tool_guard("delete_post")
    async def delete_post(
        post_id: Annotated[str, Field(min_length=1, max_length=255)],
        confirm: Annotated[bool, Field(description="Must be true to delete")] = False,
        account_id: Annotated[str | None, Field(max_length=64)] = None,
        ctx: Context = CurrentContext(),
    ) -> dict[str, Any]:
        """
        Delete one of the active (or specified) identity's own posts.

        Checks the identity's posting limits, records the attempt, and deletes
        through the ``delete.php`` confirm flow. Fails honestly if the post is
        not available (already deleted) or deletion is decommissioned.

        Args:
            post_id: Numeric post id of a post owned by the identity
            confirm: Must be set to true to actually delete (safety gate)
            account_id: Optional stored identity id to delete as (default: active)

        Returns:
            Dict with success and post_id.
        """
        require_confirm(
            confirm,
            "delete_post requires confirm=true to delete. This permanently "
            "removes a public post from your timeline.",
        )
        posting = await get_ready_posting_client(
            ctx, tool_name="delete_post", account_id=account_id
        )
        return await posting.delete_post(post_id)

    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="Save Post",
        annotations={"idempotentHint": True, "writeHint": True},
        tags={"posts", "write"},
    )
    @tool_guard("save_post")
    async def save_post(
        post_id: Annotated[str, Field(min_length=1, max_length=255)],
        confirm: Annotated[bool, Field(description="Must be true to save")] = False,
        account_id: Annotated[str | None, Field(max_length=64)] = None,
        ctx: Context = CurrentContext(),
    ) -> dict[str, Any]:
        """
        Save/bookmark a post to the active (or specified) identity's Saved.

        Best-effort: the legacy save endpoint submits the mutation but cannot
        always confirm it synchronously — the result tells you to verify in
        the Saved collection.

        Args:
            post_id: Numeric post id to save
            confirm: Must be set to true to actually save (safety gate)
            account_id: Optional stored identity id (default: active)

        Returns:
            Dict with success, post_id, and action.
        """
        require_confirm(
            confirm,
            "save_post requires confirm=true to save. This adds the post to your Saved collection.",
        )
        posting = await get_ready_posting_client(ctx, tool_name="save_post", account_id=account_id)
        return await posting.save_post(post_id)

    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="Unsave Post",
        annotations={"destructiveHint": True, "idempotentHint": True, "writeHint": True},
        tags={"posts", "write"},
    )
    @tool_guard("unsave_post")
    async def unsave_post(
        post_id: Annotated[str, Field(min_length=1, max_length=255)],
        confirm: Annotated[bool, Field(description="Must be true to unsave")] = False,
        account_id: Annotated[str | None, Field(max_length=64)] = None,
        ctx: Context = CurrentContext(),
    ) -> dict[str, Any]:
        """
        Remove a post from the active (or specified) identity's Saved.

        Best-effort: the legacy save endpoint submits the mutation but cannot
        always confirm it synchronously — the result tells you to verify in
        the Saved collection.

        Args:
            post_id: Numeric post id to unsave
            confirm: Must be set to true to actually unsave (safety gate)
            account_id: Optional stored identity id (default: active)

        Returns:
            Dict with success, post_id, and action.
        """
        require_confirm(
            confirm,
            "unsave_post requires confirm=true to unsave. This removes the "
            "post from your Saved collection.",
        )
        posting = await get_ready_posting_client(
            ctx, tool_name="unsave_post", account_id=account_id
        )
        return await posting.unsave_post(post_id)
