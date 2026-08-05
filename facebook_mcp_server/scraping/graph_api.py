"""Graph API write transport for facebook-lyr.

Facebook decommissioned the entire legacy ajax write surface (``updatestatus.php``,
``ufi_composer.php``, ``typ.php``, ``mark_seen.php`` all 404) while the read
surface (embedded JSON / Relay) survives — see the mutating-tool audit. The
only robust mutation path is the official Graph API.

This transport mirrors the write-method signatures and return shapes of
``FacebookScrapeClient`` so the tool layer is interface-identical (design
memory #2208). It replaces only the decommissioned mutations — text/photo/
video/carousel posts, comments, deletes — and delegates messenger + save/
unsave to an optional cookie client (#2209), since Graph API has no messenger
or "save" surface.

Auth: an app/user access token from ``FACEBOOK_GRAPH_API_TOKEN``. The target
page id comes from ``FACEBOOK_GRAPH_API_PAGE_ID``, else the recorded active
Page, else ``GET /me/accounts``.
"""

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

from curl_cffi.requests import AsyncSession

from facebook_mcp_server.core.exceptions import (
    AuthenticationError,
    NetworkError,
    RateLimitError,
    ScrapingError,
)

logger = logging.getLogger(__name__)

GRAPH_API_VERSION = "v20.0"
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"
TOKEN_KEY = "FACEBOOK_GRAPH_API_TOKEN"
PAGE_ID_KEY = "FACEBOOK_GRAPH_API_PAGE_ID"


class GraphApiClient:
    """Facebook Graph API write client.

    Args:
        token: App or user access token for the posting identity.
        page_id: Optional explicit page id override (else active Page / /me/accounts).
        cookie_client: Optional ``FacebookScrapeClient`` for messenger +
            save/unsave operations that Graph API cannot express.
    """

    def __init__(
        self,
        token: str,
        *,
        page_id: str | None = None,
        cookie_client: Any | None = None,
    ) -> None:
        self._token = token.strip()
        if not self._token:
            raise AuthenticationError(
                "FACEBOOK_GRAPH_API_TOKEN is empty. Graph API writes need a "
                "valid app or user access token."
            )
        self._explicit_page_id = (page_id or "").strip() or None
        self._cookie_client = cookie_client
        self._resolved_page_id: str | None = None
        self._resolved_page_token: str | None = None
        self._page_scope_locked = False
        self._session: AsyncSession | None = None

    # ── session ────────────────────────────────────────────────────────────

    async def _session_c(self) -> AsyncSession:
        if self._session is None:
            from facebook_mcp_server.scraping import identity

            self._session = AsyncSession(
                impersonate=identity.IMPERSONATE,
                timeout=90.0,
                allow_redirects=True,
            )
        return self._session

    async def close(self) -> None:
        if self._session is not None:
            try:
                self._session.close()
            except Exception:
                pass
            self._session = None

    async def __aenter__(self) -> "GraphApiClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    # ── page resolution ────────────────────────────────────────────────────

    async def _resolve_page(self) -> tuple[str, str]:
        """Return (page_id, page_token) for the posting identity.

        Priority: explicit page id override > recorded active Page > the
        token's first manageable Page (via ``/me/accounts``). When the token
        is a user token, the page-scoped token from ``/me/accounts`` is used.
        """
        if self._resolved_page_id:
            return self._resolved_page_id, self._resolved_page_token or self._token

        candidates: list[dict[str, Any]] = []
        try:
            candidates = await self._graph_get("/me/accounts")
        except AuthenticationError:
            raise
        except Exception as exc:  # noqa: BLE001 — token may be page-scoped
            logger.debug("Could not list /me/accounts (%s); using raw token", exc)

        active_page_id = None
        if self._explicit_page_id:
            active_page_id = self._explicit_page_id
        else:
            try:
                from facebook_mcp_server.session_state import load_active_page

                active = load_active_page()
                active_page_id = active.get("page_id")
            except Exception as exc:  # noqa: BLE001
                logger.debug("Could not load active page (%s)", exc)

        page = None
        if active_page_id:
            page = next(
                (p for p in candidates if str(p.get("id")) == str(active_page_id)),
                None,
            )
        if page is None and candidates:
            page = candidates[0]

        if page:
            page_id = str(page.get("id"))
            page_token = page.get("access_token") or self._token
            self._resolved_page_id = page_id
            self._resolved_page_token = page_token or self._token
            return page_id, self._resolved_page_token

        # No /me/accounts and no active page: assume a page-scoped token.
        page_id = self._explicit_page_id or ""
        if not page_id:
            raise AuthenticationError(
                "Could not resolve a target Page. Set FACEBOOK_GRAPH_API_PAGE_ID "
                "to the page id, or use a token that can list /me/accounts."
            )
        self._resolved_page_id = page_id
        self._resolved_page_token = self._token
        return page_id, self._token

    # ── request plumbing ───────────────────────────────────────────────────

    async def _graph_request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        data: dict[str, Any] | None = None,
        multipart: Any | None = None,
    ) -> Any:
        url = f"{GRAPH_BASE}{path}"
        session = await self._session_c()
        try:
            resp = await session.request(
                method,
                url,
                params={"access_token": token or self._token},
                data=data,
                multipart=multipart,
            )
        except Exception as exc:
            raise NetworkError(f"Graph API request to {path} failed: {exc}") from exc

        try:
            body = resp.json()
        except Exception:
            body = {"error": {"message": f"Non-JSON response (HTTP {resp.status_code})"}}

        if isinstance(body, dict) and body.get("error"):
            self._raise_graph_error(body["error"])
        if resp.status_code in (401, 403):
            raise AuthenticationError(
                f"Graph API rejected the token (HTTP {resp.status_code}). "
                "Check FACEBOOK_GRAPH_API_TOKEN."
            )
        if resp.status_code == 429:
            raise RateLimitError("Graph API rate-limited the request")
        if resp.status_code >= 500:
            raise NetworkError(f"Graph API returned HTTP {resp.status_code}")
        return body

    def _raise_graph_error(self, error: dict[str, Any]) -> None:
        code = error.get("code")
        message = error.get("message", "unknown Graph API error")
        subcode = error.get("error_subcode")

        if code == 190 or subcode in (459, 460, 463):
            raise AuthenticationError(
                f"Graph API token invalid or expired (code {code}): {message}"
            )
        if code in (4, 17, 32, 341, 368, 613) or subcode in (2446071, 2446002):
            raise RateLimitError(f"Graph API rate limit (code {code}): {message}")
        if code in (10, 200):
            raise AuthenticationError(
                f"Graph API permission error (code {code}): {message}"
            )
        raise ScrapingError(f"Graph API error (code {code}): {message}")

    async def _graph_get(self, path: str) -> list[dict[str, Any]]:
        body = await self._graph_request("GET", path)
        data = body.get("data", []) if isinstance(body, dict) else []
        return [d for d in data if isinstance(d, dict)]

    # ── media upload ───────────────────────────────────────────────────────

    async def _upload_photos(self, sources: list[str]) -> list[str]:
        """Upload photos (published=false) and return their Graph photo ids."""
        page_id, token = await self._resolve_page()
        photo_ids: list[str] = []
        for source in sources:
            photo_id = await self._upload_single_photo(page_id, token, source)
            photo_ids.append(photo_id)
        return photo_ids

    async def _upload_single_photo(
        self, page_id: str, token: str, source: str
    ) -> str:
        if source.startswith(("http://", "https://")):
            body = await self._graph_request(
                "POST",
                f"/{page_id}/photos",
                token=token,
                data={"url": source, "published": "false"},
            )
        else:
            local = Path(source).expanduser()
            if not local.exists():
                raise ScrapingError(f"Local file not found: {source}")
            import curl_cffi

            mime = (
                "image/png" if local.suffix.lower() in (".png",) else "image/jpeg"
            )
            mp = curl_cffi.CurlMime()
            mp.addpart(
                name="source",
                content_type=mime,
                filename=local.name,
                data=local.read_bytes(),
            )
            body = await self._graph_request(
                "POST",
                f"/{page_id}/photos",
                token=token,
                multipart=mp,
            )
        photo_id = body.get("id") if isinstance(body, dict) else None
        if not photo_id:
            raise ScrapingError(
                "Graph API photo upload returned no photo id: "
                f"{body if isinstance(body, dict) else 'empty response'}"
            )
        return str(photo_id)

    # ── write surface (Graph) ──────────────────────────────────────────────

    async def create_post(self, status: str) -> dict[str, Any]:
        """Publish a text post to the resolved Page's feed."""
        page_id, token = await self._resolve_page()
        body = await self._graph_request(
            "POST",
            f"/{page_id}/feed",
            token=token,
            data={"message": status, "published": "true"},
        )
        post_id = body.get("id") if isinstance(body, dict) else None
        if not post_id:
            raise ScrapingError(
                "Graph API text post returned no id: "
                f"{body if isinstance(body, dict) else 'empty response'}"
            )
        return {"success": True, "post_id": str(post_id)}

    async def create_comment(self, post_id: str, text: str) -> dict[str, Any]:
        """Comment on a post or page."""
        _, token = await self._resolve_page()
        body = await self._graph_request(
            "POST",
            f"/{post_id}/comments",
            token=token,
            data={"message": text},
        )
        comment_id = body.get("id") if isinstance(body, dict) else None
        if not comment_id:
            raise ScrapingError(
                "Graph API comment returned no id: "
                f"{body if isinstance(body, dict) else 'empty response'}"
            )
        return {"success": True, "comment_id": str(comment_id)}

    async def create_photo_post(
        self, source: str, message: str = ""
    ) -> dict[str, Any]:
        """Publish a photo post (single-photo carousel)."""
        photo_ids = await self._create_media_post([source], message)
        photo_ids["attachment_id"] = (photo_ids.get("attachment_ids") or [None])[0]
        return photo_ids

    async def create_carousel_post(
        self, sources: list[str], message: str = ""
    ) -> dict[str, Any]:
        """Publish a multi-photo carousel post."""
        result = await self._create_media_post(sources, message)
        result["count"] = len(sources)
        return result

    async def _create_media_post(
        self, sources: list[str], message: str = ""
    ) -> dict[str, Any]:
        page_id, token = await self._resolve_page()
        photo_ids = await self._upload_photos(sources)
        attached = [{"media_fbid": pid} for pid in photo_ids]
        body = await self._graph_request(
            "POST",
            f"/{page_id}/feed",
            token=token,
            data={
                "message": message,
                "published": "true",
                "attached_media": json.dumps(attached),
            },
        )
        post_id = body.get("id") if isinstance(body, dict) else None
        if not post_id:
            raise ScrapingError(
                "Graph API media post returned no id: "
                f"{body if isinstance(body, dict) else 'empty response'}"
            )
        return {
            "success": True,
            "post_id": str(post_id),
            "attachment_ids": photo_ids,
            "count": len(photo_ids),
        }

    async def create_video_post(
        self, source: str, message: str = ""
    ) -> dict[str, Any]:
        """Publish a video post to the resolved Page."""
        page_id, token = await self._resolve_page()
        if source.startswith(("http://", "https://")):
            data = {
                "file_url": source,
                "description": message,
                "published": "true",
            }
            body = await self._graph_request(
                "POST", f"/{page_id}/videos", token=token, data=data
            )
        else:
            local = Path(source).expanduser()
            if not local.exists():
                raise ScrapingError(f"Local file not found: {source}")
            import curl_cffi

            mp = curl_cffi.CurlMime()
            mp.addpart(
                name="source",
                filename=local.name,
                data=local.read_bytes(),
            )
            mp.addpart(name="description", data=message.encode("utf-8"))
            mp.addpart(name="published", data=b"true")
            body = await self._graph_request(
                "POST", f"/{page_id}/videos", token=token, multipart=mp
            )
        video_id = body.get("id") if isinstance(body, dict) else None
        if not video_id:
            raise ScrapingError(
                "Graph API video post returned no id: "
                f"{body if isinstance(body, dict) else 'empty response'}"
            )
        return {"success": True, "post_id": str(video_id)}

    async def delete_post(self, post_id: str) -> dict[str, Any]:
        """Delete a post (or photo/comment) by its Graph id."""
        _, token = await self._resolve_page()
        body = await self._graph_request(
            "DELETE", f"/{post_id}", token=token
        )
        success = isinstance(body, dict) and body.get("success") in (True, "true", 1)
        if not success:
            raise ScrapingError(
                f"Graph API delete returned non-success: "
                f"{body if isinstance(body, dict) else 'empty response'}"
            )
        return {"success": True, "post_id": str(post_id)}

    # ── delegated surface (cookie client) ──────────────────────────────────

    async def save_post(self, post_id: str) -> dict[str, Any]:
        if self._cookie_client is None:
            raise ScrapingError(
                "save_post is not supported via the Graph API. No cookie "
                "client is available for the legacy save surface."
            )
        return await self._cookie_client.save_post(post_id)

    async def unsave_post(self, post_id: str) -> dict[str, Any]:
        if self._cookie_client is None:
            raise ScrapingError(
                "unsave_post is not supported via the Graph API. No cookie "
                "client is available for the legacy unsave surface."
            )
        return await self._cookie_client.unsave_post(post_id)

    async def send_message(self, thread_id: str, body: str) -> dict[str, Any]:
        if self._cookie_client is None:
            raise ScrapingError(
                "Messenger is not exposed via the Graph API. No cookie client "
                "is available for the legacy messenger surface."
            )
        return await self._cookie_client.send_message(thread_id, body)

    async def delete_message(
        self, thread_id: str, message_id: str
    ) -> dict[str, Any]:
        if self._cookie_client is None:
            raise ScrapingError(
                "Messenger is not exposed via the Graph API. No cookie client "
                "is available for the legacy messenger surface."
            )
        return await self._cookie_client.delete_message(thread_id, message_id)


def graph_token_from_env() -> str | None:
    """Read the Graph API token from the environment, if configured."""
    token = os.environ.get(TOKEN_KEY)
    if token and token.strip():
        return token.strip()
    return None
