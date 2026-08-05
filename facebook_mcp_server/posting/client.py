"""
Facebook posting client.

Resolves the identity to post as (explicit account > active account > default
profile session), builds the cookie-authenticated scrape client, and runs
validated, limit-checked write attempts: text posts, photo posts, video posts,
and carousels — mirroring instagram-lyr's ``posting/client.py``.

Every write is validated up-front (status length, media files, caption),
checked against the identity's posting limits, then attempted through the
legacy web composer. If the transport is decommissioned for that account, the
underlying :class:`~facebook_mcp_server.core.exceptions.ScrapingError`
surfaces honestly ("official API required") and the attempt is recorded.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from facebook_mcp_server.core import AuthenticationError, ScrapingError
from facebook_mcp_server.multi_account import (
    check_account_posting_limits,
    get_account_cookies,
    get_active_account,
    record_post_attempt,
)
from facebook_mcp_server.posting.media_processor import MediaProcessor
from facebook_mcp_server.posting.validators import PostingValidator

logger = logging.getLogger(__name__)


class PostingClient:
    """Cookie-authenticated Facebook write client with multi-account support."""

    def __init__(self, account_id: str | None = None):
        """Initialize the posting client for a specific identity.

        Args:
            account_id: Optional stored identity id. When omitted, the active
                stored account is used if configured, falling back to the
                default profile session.

        Raises:
            AuthenticationError: If no usable cookie session can be resolved
        """
        self.account_id = account_id
        self._resolved_account_id = account_id
        self.cookies = self._get_cookies()
        self.client = self._create_client()
        logger.info(
            "PostingClient initialized for account: %s", self._resolved_account_id or "active"
        )

    # ── identity resolution ────────────────────────────────────────────────

    def _get_cookies(self) -> dict[str, str]:
        """Resolve the cookie set for the target identity.

        Priority: explicit account_id > active stored account > default profile.
        """
        if self.account_id:
            cookies = get_account_cookies(self.account_id)
            if not cookies:
                # NOT an AuthenticationError: a missing stored account is a
                # config problem, not a session-expiry problem. Raising an
                # auth error would trigger the re-login flow and nuke the
                # valid default session.
                raise ScrapingError(
                    f"Stored account {self.account_id} not found or no cookies. "
                    "Add it first with add_account / import_account_from_browser."
                )
            return cookies

        active = get_active_account()
        if active:
            cookies = get_account_cookies(active.account_id)
            if cookies and "c_user" in cookies and "xs" in cookies:
                self._resolved_account_id = active.account_id
                return cookies

        # Fall back to the default profile session

        from facebook_mcp_server.cookie_import import load_cookies_from_file
        from facebook_mcp_server.drivers.browser import get_profile_dir
        from facebook_mcp_server.session_state import portable_cookie_path

        profile_dir = get_profile_dir()
        cookie_file = portable_cookie_path(profile_dir)
        if not cookie_file.exists():
            raise AuthenticationError("No Facebook session found. Run with --login to create one.")
        cookies = load_cookies_from_file(cookie_file)
        if not cookies:
            raise AuthenticationError(
                "Saved cookies are missing c_user/xs. Run with --login to re-authenticate."
            )
        return cookies

    def _create_client(self):
        """Create the cookie-authenticated scrape client.

        A cookie sink is wired so Facebook's periodic xs/fr rotations are
        persisted back into the resolved identity's cookie store (per-account
        file or the default profile cookies.json).
        """

        from facebook_mcp_server.scraping import FacebookScrapeClient

        sink = None
        target_id = self._resolved_account_id
        if target_id:
            from facebook_mcp_server.dependencies import _make_cookie_sink
            from facebook_mcp_server.multi_account import account_cookies_path

            cookie_file = account_cookies_path(target_id)
            sink = _make_cookie_sink(cookie_file, dict(self.cookies))
        else:
            try:
                from facebook_mcp_server.drivers.browser import get_profile_dir
                from facebook_mcp_server.session_state import portable_cookie_path

                cookie_file = portable_cookie_path(get_profile_dir())
                if cookie_file.exists():
                    import json as _json

                    raw = _json.loads(cookie_file.read_text())
                    sink = _make_cookie_sink(cookie_file, raw)
            except Exception:
                logger.debug("No cookie sink for default profile", exc_info=True)

        scrape_client = FacebookScrapeClient(self.cookies, cookie_sink=sink)

        # Graph API write transport (token-configured): the legacy ajax write
        # surface is decommissioned (see the mutating-tool audit), so when a
        # FACEBOOK_GRAPH_API_TOKEN is present, route the decommissioned
        # mutations through the official Graph API and keep messenger + save/
        # unsave on the cookie client.
        from facebook_mcp_server.scraping.graph_api import (
            GraphApiClient,
            graph_token_from_env,
        )

        token = graph_token_from_env()
        if token:
            logger.info("Graph API token present — using GraphApiClient transport")
            return GraphApiClient(
                token,
                page_id=os.environ.get("FACEBOOK_GRAPH_API_PAGE_ID") or None,
                cookie_client=scrape_client,
            )
        return scrape_client

    # ── posting helpers ────────────────────────────────────────────────────

    def _ensure_can_post(self) -> None:
        """Raise if the resolved identity is over its posting limits."""
        target_id = self._resolved_account_id
        if not target_id:
            return
        can_post, reason = check_account_posting_limits(target_id)
        if not can_post:
            raise ScrapingError(reason or "Posting limits prevent this action")

    def _record(self, media_type: str, success: bool, **extra: Any) -> None:
        """Record a post attempt against the resolved identity."""
        target_id = self._resolved_account_id
        if not target_id:
            return
        record_post_attempt(
            account_id=target_id,
            media_type=media_type,
            success=success,
            post_id=extra.get("post_id"),
            error_message=extra.get("error_message"),
        )

    # ── comments (engage) ──────────────────────────────────────────────────

    async def comment_on_post(self, post_id: str, text: str) -> dict[str, Any]:
        """Comment on a post as the identity (validated, limit-checked, recorded).

        Args:
            post_id: Numeric post id
            text: Comment text

        Returns:
            Dict with success and comment_id
        """
        if not post_id or not post_id.strip():
            raise ScrapingError("post_id cannot be empty")
        valid, msg = PostingValidator.validate_comment(text)
        if not valid:
            raise ScrapingError(msg)
        self._ensure_can_post()
        try:
            result = await self.client.create_comment(post_id, text)
            self._record("comment", True, post_id=result.get("comment_id"))
            return result
        except Exception as e:
            self._record("comment", False, error_message=str(e))
            raise

    # ── messenger (send) ────────────────────────────────────────────────────

    async def send_message(self, thread_id: str, body: str) -> dict[str, Any]:
        """Send a Messenger text message as the identity (validated, recorded).

        Args:
            thread_id: Conversation id
            body: Message text

        Returns:
            Dict with success, message_id, and thread_id
        """
        if not thread_id or not thread_id.strip():
            raise ScrapingError("thread_id cannot be empty")
        valid, msg = PostingValidator.validate_comment(body)
        if not valid:
            raise ScrapingError(msg)
        self._ensure_can_post()
        try:
            result = await self.client.send_message(thread_id, body)
            self._record("message", True, post_id=result.get("message_id"))
            return result
        except Exception as e:
            self._record("message", False, error_message=str(e))
            raise

    # ── messenger (delete) ─────────────────────────────────────────────────

    async def delete_message(self, thread_id: str, message_id: str) -> dict[str, Any]:
        """Delete (unsend) a Messenger message (validated, limit-checked, recorded).

        Args:
            thread_id: Conversation id
            message_id: The message id to delete (from get_conversation_messages)

        Returns:
            Dict with success, thread_id, and message_id
        """
        if not thread_id or not thread_id.strip():
            raise ScrapingError("thread_id cannot be empty")
        if not message_id or not message_id.strip():
            raise ScrapingError("message_id cannot be empty")
        self._ensure_can_post()
        try:
            result = await self.client.delete_message(thread_id, message_id)
            self._record("message_delete", True, post_id=message_id)
            return result
        except Exception as e:
            self._record("message_delete", False, post_id=message_id, error_message=str(e))
            raise

    # ── post lifecycle (delete / save) ─────────────────────────────────────

    async def delete_post(self, post_id: str) -> dict[str, Any]:
        """Delete one of the identity's own posts (validated, recorded).

        Args:
            post_id: Numeric post id of a post owned by the identity

        Returns:
            Dict with success and post_id
        """
        if not post_id or not post_id.strip():
            raise ScrapingError("post_id cannot be empty")
        self._ensure_can_post()
        try:
            result = await self.client.delete_post(post_id)
            self._record("delete", True, post_id=post_id)
            return result
        except Exception as e:
            self._record("delete", False, post_id=post_id, error_message=str(e))
            raise

    async def save_post(self, post_id: str) -> dict[str, Any]:
        """Save/bookmark a post (best-effort, recorded).

        Args:
            post_id: Numeric post id to save

        Returns:
            Dict with success, post_id, and action
        """
        if not post_id or not post_id.strip():
            raise ScrapingError("post_id cannot be empty")
        self._ensure_can_post()
        try:
            result = await self.client.save_post(post_id)
            self._record("save", True, post_id=post_id)
            return result
        except Exception as e:
            self._record("save", False, post_id=post_id, error_message=str(e))
            raise

    async def unsave_post(self, post_id: str) -> dict[str, Any]:
        """Remove a post from Saved (best-effort, recorded).

        Args:
            post_id: Numeric post id to unsave

        Returns:
            Dict with success, post_id, and action
        """
        if not post_id or not post_id.strip():
            raise ScrapingError("post_id cannot be empty")
        self._ensure_can_post()
        try:
            result = await self.client.unsave_post(post_id)
            self._record("unsave", True, post_id=post_id)
            return result
        except Exception as e:
            self._record("unsave", False, post_id=post_id, error_message=str(e))
            raise

    # ── text posts ─────────────────────────────────────────────────────────

    async def create_text_post(self, status: str) -> dict[str, Any]:
        """Publish a text post to the identity's timeline.

        Args:
            status: Post text

        Returns:
            Dict with success and post_id
        """
        valid, msg = PostingValidator.validate_status(status)
        if not valid:
            raise ScrapingError(msg)
        self._ensure_can_post()
        try:
            result = await self.client.create_post(status)
            self._record("text", True, post_id=result.get("post_id"))
            return result
        except Exception as e:
            self._record("text", False, error_message=str(e))
            raise

    # ── photo posts ────────────────────────────────────────────────────────

    async def create_photo_post(self, source: str, message: str = "") -> dict[str, Any]:
        """Publish a photo post (local file or http(s) URL).

        Args:
            source: Local file path or URL of the image
            message: Optional caption

        Returns:
            Dict with success, post_id, and attachment_id
        """
        valid, msg = PostingValidator.validate_media_source(source)
        if not valid:
            raise ScrapingError(msg)
        valid, msg = PostingValidator.validate_caption(message)
        if not valid:
            raise ScrapingError(msg)
        self._ensure_can_post()

        processed = await self._prepare_photo(source)
        try:
            result = await self.client.create_photo_post(processed, message=message)
            self._record("photo", True, post_id=result.get("post_id"))
            return result
        except Exception as e:
            self._record("photo", False, error_message=str(e))
            raise

    # ── video posts ────────────────────────────────────────────────────────

    async def create_video_post(self, source: str, message: str = "") -> dict[str, Any]:
        """Publish a video post (local file or http(s) URL).

        Args:
            source: Local file path or URL of the video
            message: Optional caption

        Returns:
            Dict with success, post_id, and attachment_id
        """
        if source.startswith(("http://", "https://")):
            valid = True
            msg = ""
        else:
            valid, msg = PostingValidator.validate_video_path(source)
        if not valid:
            raise ScrapingError(msg)
        valid, msg = PostingValidator.validate_caption(message)
        if not valid:
            raise ScrapingError(msg)
        self._ensure_can_post()

        try:
            result = await self.client.create_video_post(source, message=message)
            self._record("video", True, post_id=result.get("post_id"))
            return result
        except Exception as e:
            self._record("video", False, error_message=str(e))
            raise

    # ── carousel posts ─────────────────────────────────────────────────────

    async def create_carousel_post(
        self,
        sources: list[str],
        message: str = "",
    ) -> dict[str, Any]:
        """Publish a multi-photo carousel post.

        Args:
            sources: List of local file paths or URLs (2-10 images)
            message: Optional caption

        Returns:
            Dict with success, post_id, and attachment_ids
        """
        valid, msg = PostingValidator.validate_carousel_paths(
            [s for s in sources if not s.startswith(("http://", "https://"))]
        )
        if not valid:
            raise ScrapingError(msg)
        valid, msg = PostingValidator.validate_caption(message)
        if not valid:
            raise ScrapingError(msg)
        self._ensure_can_post()

        processed: list[str] = []
        for source in sources:
            processed.append(await self._prepare_photo(source))

        try:
            result = await self.client.create_carousel_post(processed, message=message)
            self._record("carousel", True, post_id=result.get("post_id"))
            return result
        except Exception as e:
            self._record("carousel", False, error_message=str(e))
            raise

    # ── media preparation ──────────────────────────────────────────────────

    async def _prepare_photo(self, source: str) -> str:
        """Download (if URL) and normalize a photo to a local processed file.

        Falls back to the raw local path when Pillow is unavailable so a
        missing optional dependency never blocks the honest transport error.
        """
        import tempfile
        from pathlib import Path

        local = source
        if source.startswith(("http://", "https://")):
            from curl_cffi.requests import AsyncSession

            async with AsyncSession(timeout=60.0) as dl:
                resp = await dl.get(source)
                if resp.status_code != 200:
                    raise ScrapingError(
                        f"Could not download media from {source} (HTTP {resp.status_code})"
                    )
                content = resp.content
            filename = source.rstrip("/").split("?")[0].split("/")[-1] or "photo.jpg"
            tmp = Path(tempfile.gettempdir()) / "facebook-media" / f"dl_{filename}"
            tmp.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_bytes(content)
            local = str(tmp)

        try:
            return MediaProcessor.process_image_smart(local)
        except ImportError:
            logger.warning("Pillow unavailable — uploading raw image")
            return local
