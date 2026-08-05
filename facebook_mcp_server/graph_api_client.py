"""
Graph API write transport for the Facebook MCP Server.

Replaces the decommissioned legacy web-composer endpoints
(``updatestatus.php``, ``ufi_composer.php``, ``typ.php``, ``mark_seen.php``)
that now return 404. Reads stay on the cookie-based scrape client; the
official Graph API (graph.facebook.com/v20.0) is required to publish.

Mirrors ``FacebookScrapeClient``'s write-method signatures and return shapes
so the posting/messenger tool layer is interface-identical — the swap is
local to this transport.

Authentication is a Facebook App token supplied via ``FACEBOOK_GRAPH_API_TOKEN``
(optionally a page-scoped token via ``FACEBOOK_GRAPH_API_PAGE_ID``). Without a
token, this client is never constructed and the honest cookie-path errors
surface as before.
"""

from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import urlencode

from curl_cffi.requests import AsyncSession, CurlMime

from facebook_mcp_server.config.loaders import EnvironmentKeys
from facebook_mcp_server.core.exceptions import (
    AuthenticationError,
    NetworkError,
    RateLimitError,
    ScrapingError,
)

logger = logging.getLogger(__name__)

GRAPH_BASE_URL = "https://graph.facebook.com/v20.0"


class GraphApiClient:
    """Graph-API-backed Facebook write transport (drop-in for the scrape client).

    Publish, photo/video/carousel, comment, and deletes go through Graph API.
    Messenger and save/unsave have no Graph equivalent, so they delegate to the
    wrapped cookie client (those endpoints are not part of the decommissioned
    ajax write surface).
    """

    def __init__(self, cookies: dict[str, str] | None = None, *, cookie_client: Any = None):
        """Initialize the Graph API transport.

        Args:
            cookies: ignored; provided for interface parity.
            cookie_client: optional ``FacebookScrapeClient`` used to satisfy the
                messenger / save surfaces that the Graph API does not expose.
        """
        self._cookie_client = cookie_client
        self._token = os.environ.get(EnvironmentKeys.FACEBOOK_GRAPH_API_TOKEN, "").strip()
        self._page_id: str | None = None
        self._resolved: bool = False
        if not self._token:
            raise AuthenticationError(
                "Graph API transport requires FACEBOOK_GRAPH_API_TOKEN (a Facebook App "
                "access token). Without one, use the cookie-based transport."
            )

    # ── identity / transport resolution ────────────────────────────────

    @property
    def _as_account_id(self) -> str | None:
        """Alias to satisfy callers that read the account id."""
        return self._page_id

    async def _resolve_page(self) -> tuple[str, str] | None:
        """Return (page_id, page_access_token) for the posting identity.

        Priority:
          1. ``FACEBOOK_GRAPH_API_PAGE_ID`` env override (uses ``self._token`` as-is).
          2. Recorded active Page (``load_active_page``), resolved to a
             page-scoped token via ``/me/accounts``.
          3. First managed Page from ``/me/accounts``.
        Returns ``None`` when the token cannot reach any page.
        """
        if self._cache:
            return self._page_id_cache, self._token_cache
        env_page = os.environ.get(EnvironmentKeys.FACE_GRAPH_API_PAGE_ID, "").strip()
        if env_page:
            self._cache = True
            self._page_id_cache = env_page
            self._token_cache = self._token
            return env_page, self._token

        # Resolve from the Graph accounts list
        try:
            accounts = await self._graph("/me/accounts", params={"fields": "id,name,access_token"})
        except AuthenticationError:
            logger.warning("Graph token cannot read /me/accounts; using raw token")
            return None if not self._page_id else (self._page_id, self._token)
        except Exception as exc:
            logger.warning("Graph /me/accounts failed: %s", exc)
            return None

        pages = (accounts or {}).get("data") or []
        if not pages:
            return None

        active_page_id = self._active_page_id()
        target = None
        for page in pages:
            if str(page.get("id")) == str(active_page_id):
                target = page
                break
        if target is None:
            # explicit page-id set but not in accounts → prefer env, else first
            target = pages[0]
        page_id = str(target.get("id"))
        page_token = target.get("access_token") or self._token
        self._cache = True
        self._page_id_cache = page_id
        self._token_cache = page_token
        self._page_id = page_id
        return page_id, page_token

    @staticmethod
    def _active_page_id() -> str | None:
        try:
            from facebook_mcp_server.session_state import load_active_page
            return load_active_page().get("page_id")
        except Exception:
            return None

    # --transport────────────────────────────────────────────────────────

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        return await self._request("GET", path, params=params)

    async def _post(self, path: str, params: dict[str, Any], files: Any = None) -> dict[str, Any]:
        return await self._request("POST", path, params=params, files=files)

    async def _delete(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        return await self._request("DELETE", path, params=params)

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        files: Any = None,
    ) -> dict[str, Any]:
        token = self._token
        url = f"{GRAPH_BASE_URL}{path}"
        payload = dict(params or {})
        payload["access_token"] = token
        try:
            async with AsyncSession(timeout=120.0) as session:
                if method == "POST":
                    resp = await session.post(url, data=payload, multipart=files)
                elif method == "DELETE":
                    resp = await session.delete(url, params=payload)
                else:
                    resp = await session.get(url, params=payload)
        except Exception as exc:
            raise NetworkError(f"Graph API network error: {exc}") from exc

        return self._handle_response(resp.status_code, resp.text)

    def _handle_response(self, status: int, body: str) -> dict[str, Any]:
        import json

        try:
            data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            data = {}
        if isinstance(data, dict) and data.get("error"):
            err = data["error"]
            code = err.get("code")
            message = err.get("message", "")
            if code == 190 or "rate" in message.lower():
                raise RateLimitError(f"Graph API: {message}")
            if code in (190, 104) or "token" in message.lower():
                raise AuthenticationError(f"Graph API token error: {message}")
            if code in (4, 17, 341, 613, 368):
                raise RateLimitError(f"Graph API rate limited: {message}")
            raise GraphError(f"Graph API error {code}: {message}")
        if status >= 500:
            raise NetworkError(f"Graph API returned HTTP {status}")
        return data

    # -------- write surface --------

    async def create_post(self, status: str) -> dict[str, Any]:
        page_id, token = await self._require_page()
        data = await self._post(f"/{page_id}/feed", {"message": status, "published": "true"}, token=token)
        post_id = str(data.get("id", ""))
        return {"success": bool(post_id), "post_id": post_id, "message": status}

    async def create_photo_post(self, source: str, message: str = "") -> dict[str, Any]:
        """Publish a photo post (local file path or http(s) URL)."""
        page_id, token = await self._require_page()
        params = {"caption": message, "published": "true"}
        if source.startswith(("http://", "https://")):
            params["url"] = source
            data = await self._post(f"/{page_id}/photos", params, token=token)
        else:
            from pathlib import Path
            path = Path(source).expanduser()
            if not path.exists():
                raise ScrapingError(f"Local file not found: {source}")
            mp = _build_multipart("source", path)
            data = await self._post(f"/{page_id}/photos", params, files=mp, token=token)
        photo_id = str(data.get("id", ""))
        return {"success": bool(photo_id), "post_id": photo_id, "attachment_id": photo_id, "message": message}

    async def create_video_post(self, source: str, message: str = "") -> dict[str, Any]:
        """Publish a video post (local file path or http(s) URL)."""
        page_id, token = await self._require_page()
        params = {"description": message, "published": "true"}
        if source.startswith(("http://", "https://")):
            params["file_url"] = source
            data = await self._post(f"/{page_id}/videos", params, token=token)
        else:
            from pathlib import Path

            path = Path(source).expanduser()
            if not path.exists():
                raise ScrapingError(f"Local file not found: {source}")
            mp = _read_multipart(path)
            data = await self._post(f"/{page_id}/videos", params, files=mp, token=token)
        video_id = str(data.get("id", ""))
        return {"post_id": video_id, "attachment_id": video_id, "message": message, "success": bool(video_id)}

    async def create_carousel_post(self, sources: list[str], message: str = "") -> dict[str, Any]:
        """Publish a multi-photo carousel post (local paths or URLs)."""
        page_id, token = await self._require_page()
        media_ids: list[str] = []
        # Upload each photo unpublished, then stitch into a feed post
        for source in sources:
            params = {"published": "false"}
            if source.startswith(("http://", "https://")):
                params["url"] = source
                data = await self._post(f"/{page_id}/photos", params, token=token)
            else:
                from pathlib import Path

                path = Path(source).expanduser()
                if not path.exists():
                    raise ScrapingError(f"Local file not found: {source}")
                mp = _read_multipart(path)
                data = await self._post(f"/{page_id}/photos", params, files=mp, token=token)
            media_ids.append(str(data.get("id", "")))
        if not media_ids:
            raise ScrapingError("No media uploaded for carousel")
        attached = [{"media_fbid": mid} for mid in media_ids]
        feed = await self._post(
            f"/{page_id}/feed",
            {"message": message, "published": "true", "attached_media": [attached]},
            token=token,
        )
        post_id = str(feed.get("id", ""))
        return {"success": bool(post_id), "post_id": post_id, "attachment_ids": media_ids, "message": message}

    async def create_comment(self, post_id: str, text: str) -> dict[str, Any]:
        page_id, token = await self._require_page()
        data = await self._post(f"/{post_id}/comments", {"message": text}, token=token)
        comment_id = str(data.get("id", ""))
        return {"success": bool(comment_id), "comment_id": comment_id, "message": text}

    async def delete_post(self, post_id: str) -> dict[str, Any]:
        page_id, token = await self._require_page()
        data = await self._delete(f"/{post_id}", {})
        return {"success": bool(data.get("success", False)), "post_id": post_id}

    # messenger / save — no Graph equivalent, delegate to cookie client
    async def send_message(self, thread_id: str, body: str) -> dict[str, Any]:
        _ = await self._require_page()
        if self._cookie_client is None:
            raise ScrapingError("send_message requires the cookie-based client (no Graph surface)")
        return await self._cookie_client.send_message(thread_id, body)

    async def delete_message(self, thread_id: str, message_id: str) -> dict[str, Any]:
        _ = await self._require_page()
        if self._cookie_client is None:
            raise ScrapingError("delete_message requires the cookie-based client (no Graph surface)")
        return await self._cookie_client.delete_message(thread_id, message_id)

    async def save_post(self, post_id: str) -> dict[str, Any]:
        _ = await self._require_page()
        if self._cookie_client is None:
            raise ScrapingError("save_post requires the cookie-based client (no Graph surface)")
        return await self._cookie_client.save_post(post_id)

    async def unsave_post(self, post_id: str) -> dict[str, Any]:
        _ = await self._require_page()
        if self._cookie_client is None:
            raise ScrapingError("unsave_post requires the cookie-based client (no Graph surface)")
        return await self._cookie_client.unsave_post(post_id)

    async def send_typing_indicator(self, thread_id: str) -> dict[str, Any]:
        _ = await self._require_page()
        if self._cookie_client is None:
            raise ScrapingError("send_typing_indicator requires the cookie-based client (no Graph surface)")
        return await self._cookie_client.send_typing_indicator(thread_id)

    async def mark_conversation_seen(self, thread_id: str) -> dict[str, Any]:
        _ = await self._require_page()
        if self._cookie_client is None:
            raise ScrapingError("mark_conversation_seen requires the cookie-based client (no Graph surface)")
        return await self._cookie_client.mark_conversation_seen(thread_id)

    # --------

    async def _require_page(self) -> tuple[str, str]:
        resolved = await self._resolve_page()
        if resolved is None:
            raise AuthenticationError(
                "Graph API token has no accessible Page. Provide FACEBOOK_GRAPH_API_PAGE_ID "
                "or ensure the token's App is installed on a Page."
            )
        return resolved