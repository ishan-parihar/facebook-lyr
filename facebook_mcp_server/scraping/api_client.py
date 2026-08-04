"""
Facebook private-web API client (cookie-based).

Replaces the official Graph API client with direct HTTP requests via
``curl_cffi`` using Chrome TLS impersonation to avoid Facebook's JA3
fingerprint detection that blocks plain ``httpx``/``aiohttp``.

Endpoints used (all require the ``fb_dtsg`` CSRF token, extracted from the
logged-in homepage):

- Profile/page pages: ``GET /{username}`` — embedded JSON deep-scan
- Messenger (legacy ajax, still the most reliable cookie surface):
    ``GET /ajax/mercury/threadlist_info.php``
    ``GET /ajax/mercury/thread_info.php``
    ``POST /ajax/mercury/send_messages.php``
    ``POST /ajax/mercury/mark_seen.php``
- Writes: ``POST /ajax/updatestatus.php`` (text posts),
    ``POST /ajax/ufi/ufi_composer.php`` (comments)
- Groups: ``GET /groups/{id}``
- Threads.net: anonymous public profile scraping
"""

from __future__ import annotations

import json
import logging
import random
import threading
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

from curl_cffi.requests import AsyncSession as _AsyncSession
from curl_cffi.requests import exceptions as _cffi_exc

from facebook_mcp_server.constants import (
    _WRITE_JITTER,
    _WRITE_MIN_GAP,
    FACEBOOK_BASE,
)
from facebook_mcp_server.core import (
    AuthenticationError,
    NetworkError,
    PageNotFoundError,
    RateLimitError,
    ScrapingError,
)
from facebook_mcp_server.scraping import identity, parsers

logger = logging.getLogger(__name__)

# How long to wait between API retries on 429
_RATE_LIMIT_SLEEP = 30
_MAX_RETRIES = 3

# Write pacing: minimum gap (s) between write actions + extra random jitter.
# This keeps post/comment/send cadence human, so agent loops can't burn the
# session by firing rapid write bursts.
_WRITE_LOCK = threading.Lock()
_LAST_WRITE_AT = 0.0

# Anti-bot: jitter range (seconds) between paginated requests
_JITTER_MIN = 1.5
_JITTER_MAX = 4.0


def _retry_after_delay(resp: Any) -> float:
    """Parse Facebook's Retry-After hint from a 429 response (0 if absent)."""
    try:
        headers = resp.headers
    except Exception:
        return 0.0
    for key in ("Retry-After", "retry-after"):
        raw = headers.get(key)
        if raw:
            try:
                return max(0.0, float(raw))
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def set_write_pacing(enabled: bool) -> None:
    """Global toggle for write-action pacing (used by tests)."""
    global _WRITE_PACING
    _WRITE_PACING = enabled


_WRITE_PACING = True


class FacebookScrapeClient:
    """Async HTTP client for Facebook's private-web surface.

    Usage::

        client = FacebookScrapeClient({"c_user": "...", "xs": "..."})
        profile = await client.get_profile("somepage")
        threads = await client.get_thread_list(limit=20)
        await client.send_message(thread_id, "hello")
    """

    def __init__(
        self,
        cookies: dict[str, str] | None = None,
        *,
        user_agent: str | None = None,
        impersonate: str = identity.IMPERSONATE,
        cookie_sink: Callable[[dict[str, str]], None] | None = None,
        write_pace: bool = True,
    ) -> None:
        self._cookies: dict[str, str] = cookies or {}
        self._cookie_sink = cookie_sink
        self._write_pace = write_pace
        self._user_agent = user_agent or identity.USER_AGENT
        self._fb_dtsg: str | None = None
        self._me_cache: dict[str, str] | None = None
        self._user_id = self._cookies.get("c_user", "")

        # NOTE: never send ``Accept: */*`` on HTML fetches — Facebook treats it
        # as a non-browser signal and returns an EMPTY body (200 but 0 bytes),
        # which trips the login-wall detector. A browser-like Accept works for
        # both HTML pages and ajax JSON endpoints.
        headers: dict[str, str] = {
            "User-Agent": self._user_agent,
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,image/apng,*/*;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": f"{FACEBOOK_BASE}/",
            "Origin": FACEBOOK_BASE,
            "X-FB-Connection-Quality": "EXCELLENT",
            # Mercury ajax endpoints return an empty body without this header.
            "X-Requested-With": "XMLHttpRequest",
        }
        headers.update(identity.client_hints())
        self._client = _AsyncSession(
            impersonate=impersonate,
            cookies=self._cookies,
            headers=headers,
            timeout=30.0,
            allow_redirects=True,
        )

    # ── request plumbing ────────────────────────────────────────────────────

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        retries: int = _MAX_RETRIES,
        base: str = FACEBOOK_BASE,
        check_wall: bool = True,
    ) -> Any:
        """Make a request with retry and Facebook-error handling.

        Returns the parsed JSON body for ajax endpoints, or ``None`` when the
        caller requested an HTML page (see :meth:`_get_page`).
        """
        if path.startswith(("http://", "https://")):
            url = path
        else:
            url = f"{base}{path}"
        for attempt in range(retries):
            try:
                resp = await self._client.request(method, url, params=params, data=data)
            except (
                _cffi_exc.Timeout,
                _cffi_exc.ConnectTimeout,
                _cffi_exc.ReadTimeout,
                _cffi_exc.ConnectionError,
            ) as exc:
                logger.warning(
                    "Network error on %s (attempt %d/%d): %s",
                    path,
                    attempt + 1,
                    retries,
                    exc,
                )
                if attempt < retries - 1:
                    await self._sleep(2**attempt)
                    continue
                raise NetworkError(f"Network error talking to Facebook: {exc}") from exc

            if resp.status_code == 429:
                retry_after = _retry_after_delay(resp)
                delay = max(retry_after, _RATE_LIMIT_SLEEP)
                logger.warning("Rate-limited (429) on %s — retry_after=%s", path, retry_after)
                if attempt == 0:
                    await self._sleep(delay)
                    continue
                raise RateLimitError("Facebook rate-limited this request")

            if resp.status_code == 401 or resp.status_code == 403:
                raise AuthenticationError(
                    "Facebook session rejected this request (HTTP "
                    f"{resp.status_code}). Run --login to re-authenticate."
                )

            if resp.status_code == 404:
                raise PageNotFoundError(f"Facebook returned 404 for {path}")

            if resp.status_code >= 500:
                if attempt < retries - 1:
                    await self._sleep(2**attempt)
                    continue
                raise ScrapingError(f"Facebook returned HTTP {resp.status_code} for {path}")

            if check_wall and resp.status_code == 200:
                final_url = getattr(resp, "url", "") or ""
                if parsers.detect_login_wall(resp.text, str(final_url)):
                    raise AuthenticationError(
                        "Facebook session expired (login wall). Run --login to re-authenticate."
                    )

            await self._persist_cookies()
            return resp

        msg = f"Exhausted retries for {path}"
        logger.error(msg)
        raise NetworkError(msg)

    async def _get_page(self, path: str, *, base: str = FACEBOOK_BASE) -> str:
        """Fetch an HTML page, translating login walls to AuthenticationError."""
        resp = await self._request("GET", path, base=base)
        return getattr(resp, "text", "") or ""

    async def _ajax(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        """Call an ajax endpoint and parse the JSON response.

        Ajax endpoints return JSON, not HTML — login-wall detection (which
        inspects HTML markers) is disabled for these; a rejected session on an
        ajax call surfaces as a 401/403 from :meth:`_request` instead.

        Responses may be wrapped in the ``for (;;);`` JSON hijacking prefix,
        which is stripped before parsing.
        """
        resp = await self._request(method, path, check_wall=False, **kwargs)
        body = getattr(resp, "text", "") or ""
        body = body.removeprefix("for (;;);")
        try:
            data = resp.json()
        except Exception:
            # resp.json() may not know about the prefix; try a manual parse.
            import json as _json

            try:
                data = _json.loads(body)
            except Exception:
                error_msg = parsers.parse_send_error_text(body) or body[:200]
                raise ScrapingError(f"Non-JSON ajax response from {path}: {error_msg}")
        if not isinstance(data, dict):
            raise ScrapingError(f"Unexpected ajax payload from {path}: {str(data)[:200]}")
        return data

    async def _persist_cookies(self) -> None:
        """Write any cookies the server rotated back through the sink."""
        if not self._cookie_sink:
            return
        jar = getattr(self._client, "cookies", None)
        if jar is None:
            return
        updates: dict[str, str] = {}
        try:
            for cookie in jar:
                name = getattr(cookie, "name", None)
                value = getattr(cookie, "value", None)
                if name and value:
                    updates[name] = value
        except Exception:
            return
        if not updates:
            return
        combined = {**self._cookies, **updates}
        if combined == self._cookies:
            return
        self._cookies = combined
        try:
            self._cookie_sink(combined)
        except Exception:
            logger.warning("Cookie write-back failed", exc_info=True)

    async def _gate_write(self) -> None:
        """Enforce a human-like minimum gap between write actions."""
        if not (self._write_pace and _WRITE_PACING):
            return
        global _LAST_WRITE_AT
        gap = random.uniform(_WRITE_MIN_GAP, _WRITE_MIN_GAP + _WRITE_JITTER)
        with _WRITE_LOCK:
            now = time.monotonic()
            elapsed = now - _LAST_WRITE_AT
            need = gap - elapsed if elapsed < gap else 0.0
            if need == 0.0:
                _LAST_WRITE_AT = now
        if need:
            await self._sleep(need)

    async def _write_with_dtsg_refresh(self, action: Callable[[], Any]) -> dict[str, Any]:
        """Run a write action, refreshing ``fb_dtsg`` once if the token was rejected.

        Facebook rotates ``fb_dtsg`` periodically; a stale token makes write
        endpoints fail with an error containing token/csrf markers. When that
        happens we clear the cached token, fetch a fresh one, and retry the
        action exactly once. Real failures propagate unchanged.
        """
        for attempt in range(2):
            try:
                result = await action()
                if isinstance(result, dict) and not result.get("success"):
                    # Some endpoints return success:false instead of raising;
                    # detect token rejection there too.
                    detail = parsers.extract_error_message(result)
                    if detail and parsers.is_bad_token_error(detail) and attempt == 0:
                        await self._refresh_dtsg()
                        continue
                return result
            except (ScrapingError, AuthenticationError) as exc:
                if attempt == 0 and parsers.is_bad_token_error(str(exc)):
                    await self._refresh_dtsg()
                    continue
                raise
        raise ScrapingError("Write action failed after fb_dtsg refresh")  # pragma: no cover

    async def _refresh_dtsg(self) -> None:
        """Clear the cached fb_dtsg and fetch a fresh token."""
        logger.info("fb_dtsg token rejected — refreshing and retrying once")
        self._fb_dtsg = None
        await self.get_fb_dtsg()

    @staticmethod
    async def _sleep(secs: float) -> None:
        import asyncio

        await asyncio.sleep(secs)

    async def close(self) -> None:
        # curl_cffi's AsyncSession exposes close() (synchronous); older versions
        # had aclose(). Prefer close() and tolerate either.
        closer = getattr(self._client, "close", None)
        if closer is None:
            closer = getattr(self._client, "aclose", None)
        if closer is not None:
            result = closer()
            if hasattr(result, "__await__"):
                await result

    # ── session / tokens ────────────────────────────────────────────────────

    async def get_fb_dtsg(self) -> str:
        """Fetch the homepage and extract the ``fb_dtsg`` CSRF token (cached)."""
        if self._fb_dtsg:
            return self._fb_dtsg
        html = await self._get_page("/")
        token = parsers.extract_fb_dtsg(html)
        if not token:
            raise AuthenticationError(
                "Could not extract fb_dtsg token — session may be invalid. "
                "Run --login to re-authenticate."
            )
        self._fb_dtsg = token
        return token

    async def validate_session(self) -> bool:
        """Check whether the current cookies produce an authenticated session."""
        try:
            html = await self._get_page("/")
            return parsers.extract_fb_dtsg(html) is not None
        except AuthenticationError:
            return False
        except Exception:
            logger.exception("Session validation failed")
            return False

    async def get_me(self) -> dict[str, str]:
        """Return the authenticated user's id, name and username."""
        if self._me_cache:
            return dict(self._me_cache)
        html = await self._get_page("/")
        uid = self._user_id or parsers.extract_user_id_from_html(html)
        username = parsers.extract_username_from_html(html) or ""
        # The homepage embeds the viewer's own actor node (id == c_user);
        # regex name extraction is unreliable because friend-suggestion names
        # appear earlier in the page — prefer the structural scan when the
        # viewer id is known, then fall back to regex patterns.
        name = (
            (parsers.extract_viewer_name(html, uid) if uid else "")
            or parsers.extract_display_name(html)
            or ""
        )
        me = {"id": uid, "name": name, "username": username}
        self._me_cache = me
        return me

    # ── account switching ───────────────────────────────────────────────────

    async def get_switchable_accounts(self) -> dict[str, Any]:
        """List every identity the session can switch to (pages, profiles).

        Facebook's account switcher page (``/accounts/``) embeds the full list
        as Relay data. This is the discovery source an agent uses to see which
        Pages the logged-in user administers and which linked profiles exist.
        """
        html = await self._get_page("/accounts/")
        accounts = parsers.parse_switchable_accounts(html)
        return {
            "accounts": accounts,
            "total": len(accounts),
            "note": (
                "These are the identities Facebook allows this session to "
                "switch to. Switching the acting identity requires the session "
                "cookie set for that identity; add it via the multi-account "
                "tools (import_account_from_browser / add_account)."
            ),
        }

    async def switch_account(self, account_id: str) -> dict[str, Any]:
        """Attempt to switch the acting identity to another account/page.

        Modern Facebook has decommissioned the legacy ``accounts_switch``
        ajax endpoint (404 on this surface); the modern switch is a private
        GraphQL mutation with rotating doc_ids. Cookie scraping cannot switch
        identity server-side — the acting identity is determined by which
        cookie set is loaded. This method surfaces that honestly and points
        the agent at the multi-account cookie store.
        """
        raise ScrapingError(
            "Switching identity server-side is decommissioned (the legacy "
            "accounts_switch endpoint returns 404). To act as another "
            "identity, add its cookies with `add_account` or "
            "`import_account_from_browser`, then `switch_active_account`."
        )

    async def switch_to_page(self, page_id: str) -> dict[str, Any]:
        """Attempt to switch into a Page as the acting identity (page voice).

        Historically the ``m_page_voice`` cookie made Facebook act as a Page.
        Modern web surfaces ignore that cookie (the actor stays the user), so
        this surfaces the honest limitation and points at the multi-account
        store for a page-scoped session.
        """
        raise ScrapingError(
            "Page-voice switching via cookies is decommissioned (the "
            "m_page_voice mechanism is ignored by the modern web surface). "
            "To act as a Page, import a session whose c_user is that Page's "
            "identity via the multi-account tools."
        )

    # ── profiles / pages ────────────────────────────────────────────────────

    async def get_profile(self, username: str) -> dict[str, Any]:
        """Fetch a profile/page summary (bio, follower counts)."""
        safe = quote(username.lstrip("@"), safe="")
        html = await self._get_page(f"/{safe}/")
        profile = parsers.parse_profile(html, username)
        if not profile.get("name") and not profile.get("id"):
            raise PageNotFoundError(f"Could not parse profile for @{username}")
        return profile

    async def get_profile_posts(self, username: str, limit: int = 25) -> dict[str, Any]:
        """Fetch recent posts from a profile/page timeline."""
        safe = quote(username.lstrip("@"), safe="")
        html = await self._get_page(f"/{safe}/")
        posts = parsers.parse_posts(html, username, limit=limit)
        return {
            "username": username,
            "url": f"{FACEBOOK_BASE}/{safe}/",
            "posts": posts,
            "total": len(posts),
            "has_more": False,
            "note": (
                "Timeline pagination via cookie scraping is limited to the "
                "first page; deeper history needs the official API."
            ),
        }

    async def get_post(self, post_url: str) -> dict[str, Any]:
        """Fetch a single post's details from its story URL."""
        if not post_url.startswith(("http://", "https://")):
            post_url = f"{FACEBOOK_BASE}/{post_url.lstrip('/')}"
        html = await self._get_page(post_url)
        posts = parsers.parse_posts(html, "", limit=5)
        if not posts:
            # The post may be embedded differently on its own page — scan for
            # any dict carrying the post id path.
            raise ScrapingError(
                f"Could not parse post at {post_url}. The page structure may have changed."
            )
        post = dict(posts[0])
        post["url"] = post_url
        return {"url": post_url, "post": post, "total": len(posts)}

    # ── search ──────────────────────────────────────────────────────────────

    async def _search(self, kind: str, query: str, limit: int = 25) -> dict[str, Any]:
        safe_q = quote(query)
        html = await self._get_page(f"/search/{kind}/?q={safe_q}")
        results = parsers.parse_search_results(html, kind, limit=limit)
        return {
            "query": query,
            "kind": kind,
            "results": results,
            "total": len(results),
            "url": f"{FACEBOOK_BASE}/search/{kind}/?q={safe_q}",
        }

    async def search_people(self, query: str, limit: int = 25) -> dict[str, Any]:
        return await self._search("people", query, limit=limit)

    async def search_pages(self, query: str, limit: int = 25) -> dict[str, Any]:
        return await self._search("pages", query, limit=limit)

    async def search_groups(self, query: str, limit: int = 25) -> dict[str, Any]:
        return await self._search("groups", query, limit=limit)

    async def search_posts(self, query: str, limit: int = 25) -> dict[str, Any]:
        """Search public posts by keyword.

        The ``/search/posts/`` page embeds real feed stories (``comet_sections``
        nodes) rather than the entity-shaped results of people/page/group
        search, so it is parsed with :func:`parsers.parse_posts`.
        """
        safe_q = quote(query)
        html = await self._get_page(f"/search/posts/?q={safe_q}")
        posts = parsers.parse_posts(html, "search", limit=limit)
        return {
            "query": query,
            "kind": "posts",
            "posts": posts,
            "total": len(posts),
            "url": f"{FACEBOOK_BASE}/search/posts/?q={safe_q}",
        }

    # ── groups ──────────────────────────────────────────────────────────────

    async def get_group(self, group_id: str) -> dict[str, Any]:
        """Fetch a group summary."""
        html = await self._get_page(f"/groups/{group_id}/")
        return parsers.parse_group(html, group_id)

    async def get_group_posts(self, group_id: str, limit: int = 25) -> dict[str, Any]:
        """Fetch recent posts from a group feed.

        Group feed posts load client-side on the modern web surface; the
        initial group page embeds metadata only. Posts are parsed from the
        embedded JSON when present, otherwise an empty result with an honest
        note is returned.
        """
        html = await self._get_page(f"/groups/{group_id}/")
        posts = parsers.parse_posts(html, f"groups/{group_id}", limit=limit)
        return {
            "group_id": group_id,
            "url": f"{FACEBOOK_BASE}/groups/{group_id}/",
            "posts": posts,
            "total": len(posts),
            "note": (
                ""
                if posts
                else "Group feed posts load client-side and are not embedded in "
                "the initial page HTML; the official API is required to read "
                "group posts."
            ),
        }

    async def get_group_members(self, group_id: str, limit: int = 25) -> dict[str, Any]:
        """Fetch members of a group via the legacy browser-list ajax endpoint.

        Best-effort: the endpoint still answers for groups the session can
        view, but Facebook may return a generic error for private or migrated
        groups — the result then carries an empty list plus an honest note.
        """
        dtsg = await self.get_fb_dtsg()
        try:
            data = await self._ajax(
                "POST",
                "/ajax/browser/list/group_members/",
                data={
                    "fb_dtsg": dtsg,
                    "gid": group_id,
                    "page": "1",
                    "limit": str(limit),
                },
            )
        except PageNotFoundError:
            raise ScrapingError(
                "The group member list endpoint is decommissioned for this "
                "session (group_members returns 404). The official Graph API "
                "is required to enumerate members."
            ) from None
        if data.get("error"):
            return {
                "group_id": group_id,
                "members": [],
                "total": 0,
                "error": str(
                    data.get("errorDescription") or data.get("errorSummary") or data["error"]
                ),
                "note": (
                    "The member list endpoint returned an error for this "
                    "group (private groups / migrated surfaces). The official "
                    "Graph API is required to enumerate members."
                ),
            }
        members = parsers.parse_group_members(data, limit=limit)
        return {
            "group_id": group_id,
            "members": members,
            "total": len(members),
            "note": (
                ""
                if members
                else "Member data loads client-side on the modern surface and "
                "the legacy endpoint returned no parseable members; the "
                "official API is required for the full member list."
            ),
        }

    # ── notifications / events (read) ──────────────────────────────────────

    async def get_notifications(self, limit: int = 25) -> dict[str, Any]:
        """Fetch the authenticated identity's recent notifications."""
        html = await self._get_page("/notifications/")
        notifications = parsers.parse_notifications(html, limit=limit)
        return {
            "notifications": notifications,
            "total": len(notifications),
            "note": (
                ""
                if notifications
                else "Notification rows load client-side on this surface; only "
                "embedded rows could be parsed."
            ),
        }

    async def get_events(self, limit: int = 25) -> dict[str, Any]:
        """Fetch upcoming events from the events hub page."""
        html = await self._get_page("/events/")
        events = parsers.parse_events(html, limit=limit)
        return {
            "events": events,
            "total": len(events),
            "note": (
                ""
                if events
                else "Event details load client-side on this surface; only "
                "embedded event rows could be parsed."
            ),
        }

    # ── messenger ───────────────────────────────────────────────────────────

    async def get_thread_list(self, limit: int = 25) -> dict[str, Any]:
        """List Messenger conversations.

        Primary: legacy ``threadlist_info.php``. On accounts where Messenger
        has migrated to E2EE (the mercury endpoint returns an empty payload),
        falls back to parsing the ``message_threads/`` page HTML, which embeds
        the conversation ids.
        """
        dtsg = await self.get_fb_dtsg()
        data = await self._ajax(
            "GET",
            "/ajax/mercury/threadlist_info.php",
            params={"dpr": "1", "fb_dtsg": dtsg},
        )
        threads = parsers.parse_thread_list(data)
        source = "mercury"
        if not threads:
            html = await self._get_page("/message_threads/")
            threads = parsers.parse_thread_list_from_html(html, limit=limit)
            source = "html" if threads else source
        return {
            "conversations": threads[:limit],
            "total": len(threads[:limit]),
            "source": source,
            "note": (
                ""
                if source == "mercury"
                else "Messenger uses E2EE for this account; conversation ids "
                "were extracted from the web inbox (message bodies require a "
                "browser session)."
            ),
        }

    async def get_thread(self, thread_id: str) -> dict[str, Any]:
        """Fetch messages for a conversation.

        Primary: legacy ``thread_info.php``. Falls back to the thread page
        HTML (best-effort; E2EE message bodies are not decryptable server-side).
        """
        dtsg = await self.get_fb_dtsg()
        messages: list[dict[str, Any]] = []
        source = "mercury"
        try:
            data = await self._ajax(
                "GET",
                "/ajax/mercury/thread_info.php",
                params={
                    "messages[thread_fbid]": thread_id,
                    "fb_dtsg": dtsg,
                    "dpr": "1",
                },
            )
            messages = parsers.parse_thread_messages(data)
        except (ScrapingError, PageNotFoundError):
            logger.info("mercury thread_info unavailable for %s — using HTML", thread_id)
        if not messages:
            html = await self._get_page(f"/messages/t/{thread_id}/")
            messages = parsers.parse_thread_page_messages(html)
            source = "html" if messages else source
        return {
            "thread_id": thread_id,
            "messages": messages,
            "total": len(messages),
            "source": source,
            "note": (
                ""
                if source == "mercury"
                else "Message bodies for E2EE conversations are encrypted and "
                "cannot be read server-side; only rendered page text is shown."
            ),
        }

    async def send_message(self, thread_id: str, body: str) -> dict[str, Any]:
        """Send a Messenger text message (``send_messages.php``)."""
        await self._gate_write()
        return await self._write_with_dtsg_refresh(lambda: self._send_message_once(thread_id, body))

    async def _send_message_once(self, thread_id: str, body: str) -> dict[str, Any]:
        dtsg = await self.get_fb_dtsg()
        try:
            data = await self._ajax(
                "POST",
                "/ajax/mercury/send_messages.php",
                data={
                    "fb_dtsg": dtsg,
                    "body": json.dumps({"text": body}),
                    "threads[thread_fbid]": json.dumps([thread_id]),
                    "ttl": "86400",
                    "client": "web_messenger",
                },
            )
        except PageNotFoundError:
            raise ScrapingError(
                "Messenger sending via legacy ajax is decommissioned for this "
                "account (send_messages.php returns 404). The official "
                "Messenger API is required to send messages."
            ) from None
        result = parsers.parse_send_result(data)
        result["thread_id"] = thread_id
        if not result["success"]:
            detail = parsers.extract_error_message(data) or result["message"]
            raise ScrapingError(detail)
        return result

    async def mark_seen(self, thread_id: str) -> dict[str, Any]:
        """Mark a conversation as seen (``mark_seen.php``)."""
        return await self._write_with_dtsg_refresh(lambda: self._mark_seen_once(thread_id))

    async def _mark_seen_once(self, thread_id: str) -> dict[str, Any]:
        dtsg = await self.get_fb_dtsg()
        try:
            data = await self._ajax(
                "POST",
                "/ajax/mercury/mark_seen.php",
                data={"fb_dtsg": dtsg, "thread_fbid": thread_id},
            )
        except PageNotFoundError:
            raise ScrapingError(
                "Marking seen via legacy ajax is decommissioned for this "
                "account (mark_seen.php returns 404). The official Messenger "
                "API is required."
            ) from None
        ok = data.get("success") is True or data.get("ok") is True
        if not ok:
            raise ScrapingError(parsers.extract_error_message(data) or "mark_seen failed")
        return {"thread_id": thread_id, "success": True}

    async def send_typing(self, thread_id: str, typing: bool = True) -> dict[str, Any]:
        """Send a typing indicator (``messaging/typ.php``)."""
        return await self._write_with_dtsg_refresh(
            lambda: self._send_typing_once(thread_id, typing)
        )

    async def _send_typing_once(self, thread_id: str, typing: bool) -> dict[str, Any]:
        dtsg = await self.get_fb_dtsg()
        try:
            data = await self._ajax(
                "POST",
                "/ajax/messaging/typ.php",
                params={"dpr": "1"},
                data={
                    "fb_dtsg": dtsg,
                    "thread_ids[0]": thread_id,
                    "typ": "1" if typing else "0",
                    "to": thread_id,
                },
            )
        except PageNotFoundError:
            raise ScrapingError(
                "Typing indicators via legacy ajax are decommissioned for this "
                "account (typ.php returns 404). The official Messenger API is "
                "required."
            ) from None
        ok = data.get("success") is True or data.get("ok") is True
        if not ok:
            raise ScrapingError(parsers.extract_error_message(data) or "typing update failed")
        return {"thread_id": thread_id, "typing": typing, "success": True}

    # ── writes ──────────────────────────────────────────────────────────────

    async def create_post(self, status: str) -> dict[str, Any]:
        """Publish a text post to the authenticated user's timeline.

        Note: posting as a Page requires the browser session to be in
        "use Facebook as" Page mode (the ``m_page_voice`` cookie). If you see
        the post land on the personal timeline instead, switch Page mode in
        the browser and re-import cookies.
        """
        await self._gate_write()
        return await self._write_with_dtsg_refresh(lambda: self._create_post_once(status))

    async def _create_post_once(self, status: str) -> dict[str, Any]:
        dtsg = await self.get_fb_dtsg()
        try:
            data = await self._ajax(
                "POST",
                "/ajax/updatestatus.php",
                data={
                    "fb_dtsg": dtsg,
                    "status": status,
                    "privacyx": "300645083384735",  # public privacy list id
                },
            )
        except PageNotFoundError:
            raise ScrapingError(
                "Timeline posting via legacy ajax is decommissioned for this "
                "account (updatestatus.php returns 404). The official Graph "
                "API is required to publish posts."
            ) from None
        ok = data.get("success") is True or data.get("ok") is True
        post_id = (
            data.get("post_id")
            or data.get("status")
            or data.get("payload", {}).get("post_id")
            or ""
        )
        if not ok:
            raise ScrapingError(parsers.extract_error_message(data) or "Post failed")
        return {
            "success": True,
            "post_id": str(post_id),
            "message": "Post published" if post_id else "Post published (id unknown)",
        }

    async def create_comment(self, post_id: str, text: str) -> dict[str, Any]:
        """Comment on a post (``ufi_composer.php``)."""
        await self._gate_write()
        return await self._write_with_dtsg_refresh(lambda: self._create_comment_once(post_id, text))

    async def _create_comment_once(self, post_id: str, text: str) -> dict[str, Any]:
        dtsg = await self.get_fb_dtsg()
        try:
            data = await self._ajax(
                "POST",
                f"/ajax/ufi/ufi_composer.php?target={post_id}",
                data={"fb_dtsg": dtsg, "comment_text": text, "target": post_id},
            )
        except PageNotFoundError:
            raise ScrapingError(
                "Commenting via legacy ajax is decommissioned for this account "
                "(ufi_composer.php returns 404). The official Graph API is "
                "required to post comments."
            ) from None
        ok = data.get("success") is True or data.get("ok") is True
        comment_id = data.get("comment_id") or data.get("payload", {}).get("commentID") or ""
        if not ok:
            raise ScrapingError(parsers.extract_error_message(data) or "Comment failed")
        return {
            "success": True,
            "comment_id": str(comment_id),
            "message": "Comment posted" if comment_id else "Comment posted (id unknown)",
        }

    async def delete_post(self, post_id: str) -> dict[str, Any]:
        """Delete one of the identity's own posts (``delete.php``)."""
        await self._gate_write()
        return await self._write_with_dtsg_refresh(lambda: self._delete_post_once(post_id))

    async def _delete_post_once(self, post_id: str) -> dict[str, Any]:
        dtsg = await self.get_fb_dtsg()
        try:
            resp = await self._request(
                "POST",
                "/delete.php",
                data={"fb_dtsg": dtsg, "post_id": post_id, "confirm": "1"},
                check_wall=False,
            )
        except PageNotFoundError:
            raise ScrapingError(
                "Post deletion via delete.php is decommissioned for this "
                "account (delete.php returns 404). The official Graph API is "
                "required to delete posts."
            ) from None
        body = getattr(resp, "text", "") or ""
        lowered = body.lower()
        # ``check_wall=False`` skips automatic login-wall detection (the
        # endpoint returns HTML, not JSON) — do it explicitly so an expired
        # session surfaces as AuthenticationError (re-login), never as a
        # false "deleted".
        if parsers.detect_login_wall(body, str(getattr(resp, "url", "") or "")):
            raise AuthenticationError(
                "Facebook session expired (login wall). Run --login to re-authenticate."
            )
        # For live posts Facebook renders the delete confirmation/processing
        # page; for missing or undeletable posts it renders an availability
        # error instead.
        if (
            "this content isn't available" in lowered
            or "content isn't available" in lowered
            or "content not found" in lowered
            or "no longer available" in lowered
        ):
            raise ScrapingError(f"Post {post_id} is not available or was already deleted.")
        if "errorSummary" in body or "something's gone wrong" in lowered:
            raise ScrapingError(f"Facebook could not delete post {post_id}: {body[:200]}")
        return {
            "success": True,
            "post_id": post_id,
            "message": (
                "Deletion submitted. Verify the post is removed — this "
                "transport cannot always confirm the delete synchronously."
            ),
        }

    async def save_post(self, post_id: str) -> dict[str, Any]:
        """Save/bookmark a post (``/ajax/saved/``). Best-effort."""
        await self._gate_write()
        return await self._write_with_dtsg_refresh(lambda: self._save_post_once(post_id, "save"))

    async def unsave_post(self, post_id: str) -> dict[str, Any]:
        """Remove a post from Saved (``/ajax/saved/``). Best-effort."""
        await self._gate_write()
        return await self._write_with_dtsg_refresh(lambda: self._save_post_once(post_id, "unsave"))

    async def _save_post_once(self, post_id: str, action: str) -> dict[str, Any]:
        dtsg = await self.get_fb_dtsg()
        try:
            resp = await self._request(
                "POST",
                "/ajax/saved/",
                data={
                    "fb_dtsg": dtsg,
                    "saved[object_id]": post_id,
                    "saved[type]": "1",
                    "action": action,
                },
                check_wall=False,
            )
        except PageNotFoundError:
            raise ScrapingError(
                "Saving via legacy ajax is decommissioned for this account "
                "(saved endpoint returns 404). The official Graph API is "
                "required."
            ) from None
        body = getattr(resp, "text", "") or ""
        # Same explicit login-wall check as delete_post: ``check_wall=False``
        # means an expired session returning the login page must not be
        # reported as a successful save.
        if parsers.detect_login_wall(body, str(getattr(resp, "url", "") or "")):
            raise AuthenticationError(
                "Facebook session expired (login wall). Run --login to re-authenticate."
            )
        return {
            "success": True,
            "post_id": post_id,
            "action": action,
            "message": (
                f"Save action '{action}' submitted for post {post_id}. "
                "Verify in the Saved collection — this transport cannot "
                "confirm the mutation synchronously."
            ),
        }

    async def delete_message(self, thread_id: str, message_id: str) -> dict[str, Any]:
        """Delete (unsend) a Messenger message (``delete_messages.php``)."""
        await self._gate_write()
        return await self._write_with_dtsg_refresh(
            lambda: self._delete_message_once(thread_id, message_id)
        )

    async def _delete_message_once(self, thread_id: str, message_id: str) -> dict[str, Any]:
        dtsg = await self.get_fb_dtsg()
        try:
            resp = await self._request(
                "POST",
                "/ajax/mercury/delete_messages.php",
                data={
                    "fb_dtsg": dtsg,
                    "message_ids[0]": message_id,
                    "thread_fbid": thread_id,
                },
                check_wall=False,
            )
        except PageNotFoundError:
            raise ScrapingError(
                "Message deletion via legacy ajax is decommissioned for this "
                "account (delete_messages.php returns 404). The official "
                "Messenger API is required."
            ) from None
        body = getattr(resp, "text", "") or ""
        # Mercury returns an EMPTY body on success for delete_messages.
        if body.strip():
            stripped = body.removeprefix("for (;;);")
            try:
                data = json.loads(stripped)
            except json.JSONDecodeError:
                raise ScrapingError(f"Unexpected delete_messages response: {body[:160]}") from None
            if isinstance(data, dict) and data.get("error"):
                detail = (
                    data.get("errorDescription")
                    or data.get("errorSummary")
                    or parsers.extract_error_message(data)
                    or str(data.get("error"))
                )
                raise ScrapingError(f"Message delete failed: {detail}")
        return {
            "success": True,
            "thread_id": thread_id,
            "message_id": message_id,
            "message": "Message deleted",
        }

    async def get_post_comments(self, post_id: str, limit: int = 25) -> dict[str, Any]:
        """Fetch comments for a post.

        Primary: legacy ``ufi_realtime.php``. On accounts/sessions where that
        endpoint has been decommissioned (404), falls back to the story page,
        which embeds the comment *count* but renders bodies client-side — the
        result then carries the count plus an honest note.
        """
        dtsg = await self.get_fb_dtsg()
        comments: list[dict[str, Any]] = []
        source = "ufi"
        try:
            data = await self._ajax(
                "GET",
                "/ajax/ufi/ufi_realtime.php",
                params={"refid": post_id, "fb_dtsg": dtsg, "__a": "1"},
            )
            comments = parsers.parse_comments(data, limit=limit)
        except (ScrapingError, PageNotFoundError):
            logger.info("ufi_realtime unavailable for %s — using story page", post_id)
        if not comments:
            page_id, story_fbid = parsers.resolve_story_params(post_id)
            html = await self._get_page(
                f"/story.php?story_fbid={quote(story_fbid)}&id={quote(page_id)}"
            )
            count = parsers.extract_comment_count(html)
            source = "story_page"
        else:
            count = None
        return {
            "post_id": post_id,
            "comments": comments,
            "total": len(comments),
            "comment_count": count,
            "source": source,
            "note": (
                ""
                if source == "ufi"
                else "Comment bodies load client-side on the modern web surface "
                "and cannot be scraped from HTML; only the comment count is "
                "available without the official API."
            ),
        }

    # ── media (composer upload, best-effort) ────────────────────────────────

    async def upload_photo(self, source: str) -> dict[str, Any]:
        """Upload a photo to the composer and return its attachment id.

        ``source`` may be a local file path or an ``http(s)://`` URL. The
        composer upload flow (``/ajax/composer/attachment/create/upload/``)
        is best-effort — Facebook may reject it for checkpointed sessions.
        """
        await self._gate_write()
        if source.startswith(("http://", "https://")):
            async with _AsyncSession(
                impersonate=identity.IMPERSONATE,
                timeout=60.0,
            ) as dl:
                resp = await dl.get(source)
                if resp.status_code != 200:
                    raise ScrapingError(
                        f"Could not download media from {source} (HTTP {resp.status_code})"
                    )
                content = resp.content
                filename = source.rstrip("/").split("?")[0].split("/")[-1] or "photo.jpg"
        else:
            from pathlib import Path

            path = Path(source).expanduser()
            if not path.exists():
                raise ScrapingError(f"Local file not found: {source}")
            content = path.read_bytes()
            filename = path.name

        dtsg = await self.get_fb_dtsg()
        mime = "image/png" if filename.lower().endswith(".png") else "image/jpeg"
        # curl_cffi multipart upload (files=) — translate HTTP failures the
        # same way the rest of the client does before parsing the body.
        final = await self._client.request(
            "POST",
            f"{FACEBOOK_BASE}/ajax/composer/attachment/create/upload/",
            params={"dpr": "1"},
            data={"fb_dtsg": dtsg, "source": "8"},
            files={"file1": (filename, content, mime)},
        )
        if final.status_code in (401, 403):
            raise AuthenticationError(
                "Facebook session rejected the photo upload (HTTP "
                f"{final.status_code}). Run --login to re-authenticate."
            )
        if final.status_code == 429:
            raise RateLimitError("Facebook rate-limited the photo upload")
        if final.status_code == 404:
            raise ScrapingError(
                "Photo upload via the legacy composer is decommissioned for "
                "this account (attachment upload returns 404). The official "
                "Graph API is required to publish photos."
            )
        if final.status_code >= 500:
            raise ScrapingError(f"Facebook returned HTTP {final.status_code} for photo upload")
        try:
            body = final.json()
        except Exception:
            raise ScrapingError(
                "Photo upload returned a non-JSON response — composer upload "
                "may require a checkpoint-free session."
            )
        if not (body.get("success") is True or body.get("ok") is True):
            raise ScrapingError(f"Photo upload failed: {body.get('error') or body}")
        payload = body.get("payload") or {}
        attachment_id = (
            payload.get("attachment_id")
            or payload.get("composer_id")
            or payload.get("video_id")
            or ""
        )
        return {
            "success": True,
            "attachment_id": str(attachment_id),
            "filename": filename,
            "message": "Photo uploaded to composer" if attachment_id else "Upload accepted",
        }

    async def create_photo_post(self, source: str, message: str = "") -> dict[str, Any]:
        """Upload a photo and publish it to the timeline (best-effort)."""
        # upload_photo() applies write pacing internally, so no extra gate here.
        return await self._write_with_dtsg_refresh(
            lambda: self._create_photo_post_once(source, message)
        )

    async def _create_photo_post_once(self, source: str, message: str) -> dict[str, Any]:
        uploaded = await self.upload_photo(source)  # upload_photo already gates
        attachment_id = uploaded.get("attachment_id", "")
        dtsg = await self.get_fb_dtsg()
        data = await self._ajax(
            "POST",
            "/ajax/updatestatus.php",
            data={
                "fb_dtsg": dtsg,
                "status": message,
                "privacyx": "300645083384735",
                "attachment_ids[0]": attachment_id,
            },
        )
        ok = data.get("success") is True or data.get("ok") is True
        post_id = data.get("post_id") or data.get("payload", {}).get("post_id") or ""
        if not ok:
            raise ScrapingError(parsers.extract_error_message(data) or "Photo post failed")
        return {
            "success": True,
            "post_id": str(post_id),
            "attachment_id": attachment_id,
            "message": "Photo post published" if post_id else "Photo post published (id unknown)",
        }

    async def create_video_post(self, source: str, message: str = "") -> dict[str, Any]:
        """Upload a video and publish it to the timeline (best-effort).

        Uses the same composer attachment upload flow as photos, sending the
        file with a video mime type. Best-effort: modern Facebook may reject
        cookie-based video uploads; failures surface as clear errors.
        """
        await self._gate_write()
        return await self._write_with_dtsg_refresh(
            lambda: self._create_video_post_once(source, message)
        )

    async def _create_video_post_once(self, source: str, message: str) -> dict[str, Any]:
        from pathlib import Path

        if source.startswith(("http://", "https://")):
            async with _AsyncSession(
                impersonate=identity.IMPERSONATE,
                timeout=120.0,
            ) as dl:
                resp = await dl.get(source)
                if resp.status_code != 200:
                    raise ScrapingError(
                        f"Could not download media from {source} (HTTP {resp.status_code})"
                    )
                content = resp.content
                filename = source.rstrip("/").split("?")[0].split("/")[-1] or "video.mp4"
        else:
            path = Path(source).expanduser()
            if not path.exists():
                raise ScrapingError(f"Local file not found: {source}")
            content = path.read_bytes()
            filename = path.name

        dtsg = await self.get_fb_dtsg()
        mime = "video/mp4"
        final = await self._client.request(
            "POST",
            f"{FACEBOOK_BASE}/ajax/composer/attachment/create/upload/",
            params={"dpr": "1"},
            data={"fb_dtsg": dtsg, "source": "3"},
            files={"file1": (filename, content, mime)},
        )
        if final.status_code in (401, 403):
            raise AuthenticationError(
                "Facebook session rejected the video upload (HTTP "
                f"{final.status_code}). Run --login to re-authenticate."
            )
        if final.status_code == 429:
            raise RateLimitError("Facebook rate-limited the video upload")
        if final.status_code == 404:
            raise ScrapingError(
                "Video upload via the legacy composer is decommissioned for "
                "this account (attachment upload returns 404). The official "
                "Graph API is required to publish videos."
            )
        if final.status_code >= 500:
            raise ScrapingError(f"Facebook returned HTTP {final.status_code} for video upload")
        try:
            body = final.json()
        except Exception:
            raise ScrapingError(
                "Video upload returned a non-JSON response — composer upload "
                "may require a checkpoint-free session."
            )
        if not (body.get("success") is True or body.get("ok") is True):
            raise ScrapingError(f"Video upload failed: {body.get('error') or body}")
        payload = body.get("payload") or {}
        attachment_id = (
            payload.get("attachment_id")
            or payload.get("composer_id")
            or payload.get("video_id")
            or ""
        )

        dtsg2 = await self.get_fb_dtsg()
        data = await self._ajax(
            "POST",
            "/ajax/updatestatus.php",
            data={
                "fb_dtsg": dtsg2,
                "status": message,
                "privacyx": "300645083384735",
                "attachment_ids[0]": attachment_id,
            },
        )
        ok = data.get("success") is True or data.get("ok") is True
        post_id = data.get("post_id") or data.get("payload", {}).get("post_id") or ""
        if not ok:
            raise ScrapingError(parsers.extract_error_message(data) or "Video post failed")
        return {
            "success": True,
            "post_id": str(post_id),
            "attachment_id": attachment_id,
            "filename": filename,
            "message": "Video post published" if post_id else "Video post published (id unknown)",
        }

    async def create_carousel_post(self, sources: list[str], message: str = "") -> dict[str, Any]:
        """Upload multiple photos and publish them as one carousel post (best-effort).

        Each source is uploaded via the composer attachment flow, then all
        attachment ids are attached to a single ``updatestatus.php`` call.
        Modern Facebook may reject cookie-based carousels; failures surface
        as clear errors.
        """
        await self._gate_write()
        return await self._write_with_dtsg_refresh(
            lambda: self._create_carousel_post_once(sources, message)
        )

    async def _create_carousel_post_once(self, sources: list[str], message: str) -> dict[str, Any]:
        attachment_ids: list[str] = []
        for source in sources:
            uploaded = await self.upload_photo(source)  # upload_photo already gates
            attachment_ids.append(uploaded.get("attachment_id", ""))
        dtsg = await self.get_fb_dtsg()
        data: dict[str, Any] = {
            "fb_dtsg": dtsg,
            "status": message,
            "privacyx": "300645083384735",
        }
        for idx, attachment_id in enumerate(attachment_ids):
            data[f"attachment_ids[{idx}]"] = attachment_id
        final = await self._ajax("POST", "/ajax/updatestatus.php", data=data)
        ok = final.get("success") is True or final.get("ok") is True
        post_id = final.get("post_id") or final.get("payload", {}).get("post_id") or ""
        if not ok:
            raise ScrapingError(parsers.extract_error_message(final) or "Carousel post failed")
        return {
            "success": True,
            "post_id": str(post_id),
            "attachment_ids": attachment_ids,
            "count": len(attachment_ids),
            "message": "Carousel post published"
            if post_id
            else "Carousel post published (id unknown)",
        }
