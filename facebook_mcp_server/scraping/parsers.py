"""HTML/JSON parsers for the Facebook private-web surface.

Facebook serves nearly all logged-in content as embedded JSON inside
``<script type="application/json">`` blocks on the page. Rather than chasing
Facebook's internal key paths (which change constantly), the parsers here do
a **recursive deep-scan**: they walk every parsed JSON blob and collect nodes
that look like profiles, posts, threads, or messages by structural shape.

This is deliberately resilient: a field moving one level deeper in the tree
does not break parsing, and each extractor is unit-testable with a fixture.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

# ─── fb_dtsg extraction ────────────────────────────────────────────────────
# Multiple known shapes, newest first.
_DTSG_PATTERNS = (
    r'"DTSGInitialData",\[\],\{"token":"([^"]+)"',
    r'"dtsg":\{"token":"([^"]+)"',
    r'name="fb_dtsg"\s+value="([^"]+)"',
    r'"LSD",\[\],\{"token":"([^"]+)"',
)

_LOGIN_WALL_MARKERS = (
    '"Log in to continue"',
    '"You must log in to continue."',
    'id="loginbutton"',
    '"checkpoint_required"',
    'action="/login/"',
)


def extract_fb_dtsg(html: str) -> str | None:
    """Extract the ``fb_dtsg`` CSRF token from homepage HTML."""
    if not html:
        return None
    for pattern in _DTSG_PATTERNS:
        m = re.search(pattern, html)
        if m:
            token = m.group(1)
            if token and not token.startswith("undefined"):
                return token
    return None


# Markers Facebook uses when the ``fb_dtsg`` token is stale/rotated. Facebook
# rotates the token periodically; matching any of these lets the client refresh
# the token and retry once instead of surfacing a confusing write failure.
_BAD_TOKEN_MARKERS = (
    "invalid token",
    "bad token",
    "token mismatch",
    "token has expired",
    "expired token",
    "security token",
    "fb_dtsg",
    "csrf",
)


def is_bad_token_error(message: str) -> bool:
    """Heuristic: does this error message indicate a stale ``fb_dtsg`` token?"""
    if not message:
        return False
    lowered = message.lower()
    return any(marker in lowered for marker in _BAD_TOKEN_MARKERS)


def detect_login_wall(html: str, final_url: str = "") -> bool:
    """Detect whether the response is a login/checkpoint wall instead of content.

    Checks both the final redirect URL (e.g. ``/login/``) and HTML markers.
    """
    path = final_url.split("?", 1)[0] if final_url else ""
    lowered = path.lower()
    if "/login" in lowered or "/checkpoint" in lowered or "login.php" in lowered:
        return True
    if not html:
        return True
    for marker in _LOGIN_WALL_MARKERS:
        if marker in html:
            return True
    # A login form combined with the absence of a logout affordance.
    return ('name="pass"' in html or 'id="loginbutton"' in html) and "logout" not in html.lower()


# ─── Embedded JSON extraction ──────────────────────────────────────────────
_SCRIPT_RE = re.compile(r"<script[^>]*>(.*?)</script>", re.DOTALL)


def parse_embedded_json(html: str) -> list[Any]:
    """Parse every JSON script block into Python objects.

    Modern Facebook pages ship their data in ``data-sjs`` ScheduledServerJS
    blocks (``{"require": [["ScheduledServerJS", ...]]}``) rather than
    ``type="application/json"`` — both are valid JSON, so every ``<script>``
    block whose content parses as JSON is included. Malformed blocks are
    skipped.
    """
    blobs: list[Any] = []
    if not html:
        return blobs
    for match in _SCRIPT_RE.finditer(html):
        raw = match.group(1).strip()
        if not raw:
            continue
        # Skip obvious non-JSON script bodies early (JS bundles, workers).
        if not raw.startswith(("{", "[")):
            continue
        try:
            blobs.append(json.loads(raw))
        except (json.JSONDecodeError, ValueError):
            continue
    return blobs


# ─── Recursive deep-scan helpers ───────────────────────────────────────────
def deep_find(
    obj: Any, predicate: Callable[[Any], bool], _seen: set[int] | None = None
) -> list[Any]:
    """Return all values in *obj* matching *predicate* (recursive)."""
    results: list[Any] = []
    seen = _seen if _seen is not None else set()
    ident = id(obj)
    if ident in seen:
        return results
    seen.add(ident)

    if predicate(obj):
        results.append(obj)

    if isinstance(obj, dict):
        for value in obj.values():
            results.extend(deep_find(value, predicate, seen))
    elif isinstance(obj, list):
        for value in obj:
            results.extend(deep_find(value, predicate, seen))
    return results


def deep_find_first(obj: Any, predicate: Callable[[Any], bool]) -> Any | None:
    """Return the first value in *obj* matching *predicate* (recursive)."""
    found = deep_find(obj, predicate)
    return found[0] if found else None


def _is_str_dict(value: Any) -> bool:
    return isinstance(value, dict) and "text" in value


def _dig(obj: Any, *keys: str) -> Any:
    """Dig through nested dicts, returning None on any miss."""
    current = obj
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


# ─── Session identity extraction (homepage) ────────────────────────────────
_ID_PATTERNS = (
    r'"userID":"(\d+)"',
    r'"actorID":"(\d+)"',
    r'"userID":"(\d{5,})"',
    r"profile.php\?id=(\d+)",
)


def extract_user_id_from_html(html: str) -> str:
    """Extract the logged-in user id from homepage HTML."""
    if not html:
        return ""
    for pattern in _ID_PATTERNS:
        m = re.search(pattern, html)
        if m:
            return m.group(1)
    return ""


_NAME_PATTERNS = (
    r'"displayName":"([^"]+)"',
    r'"name":"([^"]{2,60})","shortName"',
    r'"name":"([^"]{2,60})","short_name"',
)


def extract_display_name(html: str) -> str:
    """Extract the logged-in user's display name from homepage HTML."""
    if not html:
        return ""
    for pattern in _NAME_PATTERNS:
        m = re.search(pattern, html)
        if m:
            return m.group(1)
    return ""


_USERNAME_PATTERNS = (
    r'"username":"([A-Za-z0-9._]{2,60})"',
    r'"vanity":"([A-Za-z0-9._]{2,60})"',
)


def extract_viewer_name(html: str, user_id: str) -> str:
    """Extract the logged-in user's display name from homepage JSON.

    The homepage embeds the viewer's own actor node (``id`` == ``c_user``)
    inside the ``viewer.actor`` Relay subtree. Regex name extraction picks
    up friend-suggestion names first, so this structural scan prefers a node
    that both carries the viewer id and a ``name``.
    """
    if not html or not user_id:
        return ""

    def _is_viewer(node: Any) -> bool:
        if not isinstance(node, dict):
            return False
        if str(node.get("id") or node.get("actorID") or node.get("userID") or "") != str(user_id):
            return False
        return isinstance(node.get("name"), str) and bool(node["name"])

    node = deep_find_first(parse_embedded_json(html), _is_viewer)
    if node:
        return str(node["name"])
    return ""


def extract_username_from_html(html: str) -> str:
    """Extract the logged-in user's vanity username from homepage HTML."""
    if not html:
        return ""
    for pattern in _USERNAME_PATTERNS:
        m = re.search(pattern, html)
        if m:
            return m.group(1)
    return ""


# ─── Profile parsing ───────────────────────────────────────────────────────
def parse_profile(html: str, username: str) -> dict[str, Any]:
    """Extract a profile/page summary from an embedded-JSON profile page."""
    profile: dict[str, Any] = {
        "username": username,
        "url": f"https://www.facebook.com/{username}",
    }

    def _is_user_node(node: Any) -> bool:
        if not isinstance(node, dict):
            return False
        name = node.get("name")
        uid = node.get("userID") or node.get("actorID") or node.get("id")
        modern = node.get("__typename") == "User" or node.get("__isProfile") is not None
        legacy = "userID" in node or "actorID" in node
        return isinstance(name, str) and bool(name) and bool(uid) and (modern or legacy)

    nodes = deep_find(parse_embedded_json(html), _is_user_node)
    if nodes:
        node = nodes[0]
        profile["id"] = str(node.get("userID") or node.get("actorID") or node.get("id") or "")
        profile["name"] = node.get("name", "")
        profile["short_name"] = node.get("shortName") or node.get("alternateName") or ""
        profile_url = node.get("profileURL") or node.get("profile_url") or node.get("url")
        if profile_url:
            profile["url"] = str(profile_url)
        bio = _dig(node, "bioText", "text") or _dig(node, "introText", "text") or node.get("bio")
        if bio:
            profile["bio"] = bio

    # Follower count sometimes ships as {"count": N} next to a label.
    def _is_count_node(node: Any) -> bool:
        return (
            isinstance(node, dict)
            and isinstance(node.get("count"), (int, str))
            and isinstance(node.get("shortLabel"), str)
        )

    for node in deep_find(parse_embedded_json(html), _is_count_node):
        label = str(node.get("shortLabel", "")).lower()
        if "follower" in label and "follower_count" not in profile:
            profile["follower_count"] = node["count"]
        elif "following" in label and "following_count" not in profile:
            profile["following_count"] = node["count"]

    return profile


# ─── Post parsing ──────────────────────────────────────────────────────────
def _is_post_node(node: Any) -> bool:
    """Structural test for a feed story/post node."""
    if not isinstance(node, dict):
        return False
    if "comet_sections" not in node:
        return False
    return "creation_time" in node or "feed_units" in node or "id" in node


def parse_posts(html: str, username: str, limit: int = 25) -> list[dict[str, Any]]:
    """Collect feed posts from an embedded-JSON page, newest first.

    Nodes are de-duplicated by their id. ``limit`` caps the returned list.
    """
    blobs = parse_embedded_json(html)
    posts: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for blob in blobs:
        for node in deep_find(blob, _is_post_node):
            post_id = str(node.get("id") or "")
            if not post_id or post_id in seen_ids:
                continue
            seen_ids.add(post_id)

            sections = node.get("comet_sections", {})
            message = _dig(node, "message", "text") or ""
            if not message:
                # Walk comet_sections for any {text: ...} payload.
                for part in deep_find(sections, _is_str_dict):
                    if isinstance(part, dict) and part.get("text"):
                        message = part["text"]
                        break

            created = node.get("creation_time") or _dig(node, "creation_time")
            author = (
                _dig(
                    node,
                    "comet_sections",
                    "context_layout",
                    "story",
                    "comet_sections",
                    "actor_photo",
                    "story",
                    "actors",
                )
                or None
            )

            posts.append(
                {
                    "id": post_id,
                    "url": f"https://www.facebook.com/{username}/posts/{post_id}",
                    "text": message,
                    "created_time": int(created) if isinstance(created, (int, float)) else created,
                    "author": author if isinstance(author, dict) else {},
                }
            )
            if len(posts) >= limit:
                break
        if len(posts) >= limit:
            break

    return posts


# ─── Messenger parsing ─────────────────────────────────────────────────────
def parse_thread_list(data: Any) -> list[dict[str, Any]]:
    """Normalize the messenger ``threadlist_info.php`` JSON into thread dicts."""
    threads: list[dict[str, Any]] = []
    raw_threads = _dig(data, "threads") or _dig(data, "data", "threads") or []
    if not isinstance(raw_threads, list):
        return threads

    for t in raw_threads:
        if not isinstance(t, dict):
            continue
        participants = t.get("participants") or {}
        other_names = [
            p.get("name", "")
            for p in participants.values()
            if isinstance(p, dict) and p.get("is_self") is False
        ]
        threads.append(
            {
                "thread_id": str(t.get("thread_fbid") or t.get("thread_id") or ""),
                "name": t.get("name") or (other_names[0] if other_names else ""),
                "snippet": t.get("snippet", ""),
                "last_message": (
                    _dig(t, "last_message", "snippet")
                    or _dig(t, "last_message", "message", "text")
                    or ""
                ),
                "timestamp": t.get("timestamp", 0),
                "participant_count": len(participants) or t.get("participants_count", 0),
                "unread_count": t.get("unread_count", 0),
            }
        )
    return threads


def parse_thread_messages(data: Any) -> list[dict[str, Any]]:
    """Normalize the messenger ``thread_info.php`` JSON into message dicts."""
    messages: list[dict[str, Any]] = []
    raw_messages = (
        _dig(data, "thread", "messages")
        or _dig(data, "messages")
        or _dig(data, "data", "thread", "messages")
        or []
    )
    if not isinstance(raw_messages, list):
        return messages

    for m in raw_messages:
        if not isinstance(m, dict):
            continue
        body = (
            _dig(m, "message", "text")
            or m.get("body")
            or _dig(m, "attachments", 0, "mercury_share", "share", "description")
            or ""
        )
        sender = m.get("message_sender", {}) or {}
        messages.append(
            {
                "message_id": str(m.get("message_id") or m.get("id") or ""),
                "sender": sender.get("name", "") or sender.get("id", ""),
                "sender_id": str(sender.get("id", "")),
                "body": body,
                "timestamp": m.get("timestamp_precise", m.get("timestamp", 0)),
                "message_type": m.get("message_type", "text"),
            }
        )
    return messages


def parse_send_result(data: Any) -> dict[str, Any]:
    """Interpret the ``send_messages.php`` JSON response."""
    if not isinstance(data, dict):
        return {"success": False, "message": "Unexpected send response"}
    if data.get("success") is True or data.get("ok") is True:
        return {
            "success": True,
            "message_id": str(data.get("message_id") or ""),
            "message": "Message sent",
        }
    error = data.get("error") or data.get("err") or {}
    if isinstance(error, dict):
        detail = error.get("error_msg") or error.get("message") or error
    else:
        detail = error
    return {"success": False, "message": str(detail)}


def parse_send_error_text(text: str) -> str | None:
    """Extract a human message from a non-JSON send_messages response."""
    m = re.search(r'"error":\s*\{"error_msg":\s*"([^"]+)"', text)
    if m:
        return m.group(1)
    return None


def resolve_story_params(post_id: str) -> tuple[str, str]:
    """Split a post id into ``(page_id, post_id)`` for story URLs.

    Accepts ``{pageid}_{postid}`` numeric ids and base64 story ids (which
    decode to ``S:_I{pageid}:{postid}:{postid}``). Falls back to
    ``(post_id, post_id)`` when the shape is unknown.
    """
    if not post_id:
        return "", ""
    m = re.fullmatch(r"(\d+)_(\d+)", post_id.strip())
    if m:
        return m.group(1), m.group(2)
    try:
        import base64

        raw = base64.b64decode(post_id.strip(), validate=False).decode("utf-8", "replace")
        parts = re.findall(r"\d{5,}", raw)
        if len(parts) >= 2:
            return parts[0], parts[1]
    except Exception:  # noqa: S110 - tolerant decode
        pass
    return post_id, post_id


def extract_comment_count(html: str) -> int | None:
    """Extract the comment total from a story page's embedded feedback node.

    Modern Facebook renders comment *bodies* client-side only (no server HTML),
    but the UFI feedback node still embeds ``comment_rendering_instance.comments
    .total_count``. Both ``type="application/json"`` blocks (unescaped) and
    ``data-sjs`` blocks (escaped quotes) are covered by deep-scanning the
    parsed blobs, with a raw-HTML regex as a final fallback. Returns ``None``
    when the page has no feedback node.
    """
    if not html:
        return None

    def _is_feedback(node: Any) -> bool:
        if not isinstance(node, dict):
            return False
        comments = _dig(node, "comment_rendering_instance", "comments")
        return isinstance(comments, dict) and isinstance(comments.get("total_count"), int)

    node = deep_find_first(parse_embedded_json(html), _is_feedback)
    if node:
        return int(node["comment_rendering_instance"]["comments"]["total_count"])

    for m in re.finditer(
        r'"comment_rendering_instance"\s*:\s*\{"comments"\s*:\s*\{"total_count"\s*:\s*(\d+)',
        html,
    ):
        try:
            return int(m.group(1))
        except (TypeError, ValueError):
            continue
    return None


def parse_switchable_accounts(html: str) -> list[dict[str, Any]]:
    """Parse the identities available in Facebook's account switcher.

    The ``/accounts/`` page embeds the full switcher list as Relay data: every
    identity the logged-in session can act as — the user's own profile, Pages
    they administer, and linked profiles. Nodes carry ``__typename``
    (User/Page), ``id`` and ``name``. The current actor is included; nodes are
    de-duplicated by id.

    Returns a list of dicts: id, name, typename, is_current (bool when the
    page marks the active actor).
    """
    accounts: list[dict[str, Any]] = []
    seen: set[str] = set()
    blobs = parse_embedded_json(html)

    def _is_account_node(node: Any) -> bool:
        if not isinstance(node, dict):
            return False
        if node.get("__typename") not in ("User", "Page"):
            return False
        if not isinstance(node.get("id"), str) or not node["id"]:
            return False
        return isinstance(node.get("name"), str) and bool(node["name"])

    current_id = (
        _dig(deep_find_first(blobs, lambda n: isinstance(n, dict) and n.get("actorID")), "actorID")
        if any(isinstance(n, dict) and n.get("actorID") for n in blobs)
        else ""
    )
    if not current_id:
        current_id = extract_user_id_from_html(html)

    for node in deep_find(blobs, _is_account_node):
        node_id = str(node["id"])
        if node_id in seen:
            continue
        seen.add(node_id)
        accounts.append(
            {
                "id": node_id,
                "name": node["name"],
                "typename": node.get("__typename") or "",
                "username": node.get("username") or node.get("vanity") or "",
                "is_current": bool(current_id) and node_id == current_id,
            }
        )
    return accounts


def extract_error_message(data: Any) -> str:
    """Dig a human-readable error message out of an ajax failure payload.

    Facebook write endpoints signal failures as ``{"error": {...}}``,
    ``{"error_msg": "..."}`` or ``{"err": "..."}``. Returns "" when the
    payload does not look like an error.
    """
    if not isinstance(data, dict):
        return ""
    err = data.get("error") or data.get("err") or {}
    if isinstance(err, dict):
        return str(err.get("error_msg") or err.get("message") or err)
    if err:
        return str(err)
    for key in ("error_msg", "err_msg", "message"):
        if data.get(key):
            return str(data[key])
    return ""


# ─── Messenger HTML fallback (modern web client) ──────────────────────────
_THREAD_ID_RE = re.compile(r"entity_id=(\d+)&entity_type=10")
_THREAD_OP_RE = re.compile(r'\\?"([A-Za-z][A-Za-z ]{2,60} created this group)\\?"')


def parse_thread_list_from_html(html: str, limit: int = 25) -> list[dict[str, Any]]:
    """Extract conversation ids from the ``message_threads/`` page.

    Modern Messenger (E2EE accounts) no longer serves the legacy mercury
    ``threadlist_info.php`` payload; the inbox page embeds thread ids inside
    ``updateOrInsertThread`` ops (``media_fallback?entity_id=<id>``).
    """
    threads: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_id in _THREAD_ID_RE.findall(html):
        thread_id = raw_id.strip()
        if thread_id in seen:
            continue
        seen.add(thread_id)
        threads.append(
            {
                "thread_id": thread_id,
                "name": "",
                "snippet": "",
                "last_message": "",
                "timestamp": 0,
                "participant_count": 0,
                "unread_count": 0,
                "source": "html",
            }
        )
        if len(threads) >= limit:
            break
    return threads


def parse_thread_page_messages(html: str, limit: int = 50) -> list[dict[str, Any]]:
    """Best-effort extraction of message text from a thread page.

    Modern Messenger renders message bubbles as ``bk.components.RichText``
    trees. For E2EE conversations the bodies are encrypted client-side and
    never reach the HTML, so this returns only whatever rendered text is
    available (thread names, notifications, or plain messages on non-E2EE
    sessions). Callers should treat an empty result as expected for E2EE.
    """
    messages: list[dict[str, Any]] = []
    spans = re.findall(r'"bk.components.TextSpan"\s*:\s*\{"text"\s*:\s*"([^"]{2,400})"', html)
    spans += re.findall(r'\\"text\\"\s*:\s*\\"([^\\]{2,400})\\"', html)
    seen: set[str] = set()
    for text in spans:
        cleaned = text.replace("\\u2026", "…").strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        messages.append(
            {
                "message_id": "",
                "sender": "",
                "sender_id": "",
                "body": cleaned,
                "timestamp": 0,
                "message_type": "html",
            }
        )
        if len(messages) >= limit:
            break
    return messages


# ─── Group / search parsing ────────────────────────────────────────────────
def parse_group(html: str, group_id: str) -> dict[str, Any]:
    """Extract a group summary from an embedded-JSON group page."""
    result: dict[str, Any] = {
        "id": group_id,
        "url": f"https://www.facebook.com/groups/{group_id}",
    }
    blobs = parse_embedded_json(html)

    def _is_group_node(node: Any) -> bool:
        if not isinstance(node, dict):
            return False
        if not isinstance(node.get("name"), str) or not node["name"]:
            return False
        # Legacy pages carry ``group_id``; modern pages embed the group as a
        # profile-switch node whose ``id`` equals the group id.
        return bool(node.get("group_id")) or str(node.get("id", "")) == str(group_id)

    node = deep_find_first(blobs, _is_group_node)
    if node:
        result["name"] = node.get("name", "")
        result["group_id"] = str(node.get("group_id", group_id))
        cover = node.get("group_cover_photo")
        if cover:
            result["cover_photo"] = _dig(cover, "photo", "image", "uri") or ""
        quick_pic = _dig(node, "quick_switch_picture", "uri")
        if quick_pic:
            result["cover_photo"] = result.get("cover_photo") or quick_pic
    return result


def parse_search_results(html: str, kind: str, limit: int = 25) -> list[dict[str, Any]]:
    """Collect search results (people/pages/groups) from a search page.

    Uses a structural scan over every JSON script block. Modern search pages
    ship results as Relay entities with ``__typename`` (User/Page/Group),
    ``name`` and ``profile_url``/``url``. Bundle/worker script blobs (which
    also carry a ``name`` key but no entity marker) are excluded.
    """
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    blobs = parse_embedded_json(html)

    def _is_result_node(node: Any) -> bool:
        if not isinstance(node, dict):
            return False
        name = node.get("name")
        if not isinstance(name, str) or not name:
            return False
        # A real search entity always carries a Relay typename marker or an
        # __isProfile/__isActor mixin, plus a numeric id. Script bundles and
        # workers (which also have a "name" key) lack all of these.
        has_marker = bool(
            node.get("__typename")
            or node.get("__isProfile")
            or node.get("__isActor")
            or node.get("__isEntity")
        )
        node_id = node.get("id") or node.get("userID") or node.get("actorID")
        if not has_marker or not node_id:
            return False
        # Exclude bundle/worker script modules that happen to carry an id.
        lowered = name.lower()
        return not any(token in lowered for token in ("bundle", "worker", "module"))

    for node in deep_find(blobs, _is_result_node):
        name = str(node.get("name", ""))
        node_id = str(node.get("id") or node.get("userID") or node.get("actorID") or "")
        if not name or (node_id and node_id in seen):
            continue
        if node_id:
            seen.add(node_id)
        url = node.get("url") or node.get("profileURL") or node.get("profile_url") or ""
        results.append(
            {
                "id": node_id,
                "name": name,
                "username": node.get("username")
                or node.get("shortName")
                or node.get("vanity")
                or "",
                "url": str(url),
                "kind": kind,
                "typename": node.get("__typename") or node.get("__isProfile") or "",
            }
        )
        if len(results) >= limit:
            break
    return results


def parse_comments(data: Any, limit: int = 25) -> list[dict[str, Any]]:
    """Normalize comments from the ``ufi_realtime.php`` JSON payload.

    Tolerant deep-scan: a comment node carries ``body`` (with ``text``) and
    an ``id``. Post nodes are excluded by requiring the ``body`` shape.
    """
    comments: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _is_comment_node(node: Any) -> bool:
        if not isinstance(node, dict):
            return False
        body = node.get("body")
        if not isinstance(body, dict) or not isinstance(body.get("text"), str):
            return False
        return isinstance(node.get("id"), (str, int))

    for node in deep_find(data, _is_comment_node):
        comment_id = str(node.get("id", ""))
        if comment_id in seen:
            continue
        seen.add(comment_id)
        author = node.get("author") or {}
        if isinstance(author, dict):
            author_name = author.get("name") or ""
            author_id = str(author.get("id", ""))
        else:
            author_name, author_id = "", ""
        comments.append(
            {
                "id": comment_id,
                "author": author_name,
                "author_id": author_id,
                "text": node["body"].get("text", ""),
                "timestamp": node.get("timestamp", node.get("created_time", 0)),
            }
        )
        if len(comments) >= limit:
            break
    return comments


# ─── Notifications / Events / Group members parsing ───────────────────────


def parse_notifications(html: str, limit: int = 25) -> list[dict[str, Any]]:
    """Collect notification rows from the ``/notifications/`` page.

    The page embeds ``NotifPageNotificationRow`` nodes whose ``notif`` payload
    carries the notification id, body text, seen state, and creation time.
    Filter/header rows are excluded by requiring the ``notif.notif_id`` shape.
    """
    notifications: list[dict[str, Any]] = []
    seen: set[str] = set()
    blobs = parse_embedded_json(html)

    def _is_notif_row(node: Any) -> bool:
        if not isinstance(node, dict):
            return False
        payload = node.get("notif")
        return isinstance(payload, dict) and isinstance(payload.get("notif_id"), (str, int))

    for node in deep_find(blobs, _is_notif_row):
        payload = node.get("notif") or {}
        notif_id = str(payload.get("notif_id") or "")
        if not notif_id or notif_id in seen:
            continue
        seen.add(notif_id)
        seen_state = str(payload.get("seen_state") or "")
        notifications.append(
            {
                "id": notif_id,
                "text": _dig(payload, "body", "text") or "",
                "type": payload.get("notif_type") or "",
                "url": payload.get("url") or "",
                "seen_state": seen_state,
                "unread": "UNSEEN" in seen_state.upper(),
                "timestamp": (
                    _dig(payload, "creation_time", "timestamp") or payload.get("creation_time") or 0
                ),
            }
        )
        if len(notifications) >= limit:
            break
    return notifications


def parse_events(html: str, limit: int = 25) -> list[dict[str, Any]]:
    """Collect upcoming events from the ``/events/`` hub page.

    Event rows embed ``name`` + ``start_date_time`` (a localized display
    string) alongside the numeric id. Thin ``Event`` reference nodes (id only)
    are skipped — only fully embedded rows are returned.
    """
    events: list[dict[str, Any]] = []
    seen: set[str] = set()
    blobs = parse_embedded_json(html)

    def _is_event_node(node: Any) -> bool:
        if not isinstance(node, dict):
            return False
        if str(node.get("__typename") or "") != "Event":
            return False
        return isinstance(node.get("name"), str) and bool(node["name"])

    for node in deep_find(blobs, _is_event_node):
        event_id = str(node.get("id") or "")
        if not event_id or event_id in seen:
            continue
        seen.add(event_id)
        events.append(
            {
                "id": event_id,
                "name": node.get("name", ""),
                "start_date_time": (node.get("start_date_time") or node.get("start_time") or ""),
                "url": f"https://www.facebook.com/events/{event_id}",
            }
        )
        if len(events) >= limit:
            break
    return events


def parse_group_members(data: Any, limit: int = 25) -> list[dict[str, Any]]:
    """Normalize the ``/ajax/browser/list/group_members/`` JSON into members.

    Tolerant deep-scan: a member node carries ``id`` + ``name`` plus a
    membership marker (``__typename`` of User/GroupMember/Member, or
    ``is_admin``/``isAdmin``/``role``/``membership_state``). Returns an empty
    list when the payload is an error or lacks the member-list shape.
    """
    members: list[dict[str, Any]] = []
    seen: set[str] = set()
    if not isinstance(data, dict) or data.get("error"):
        return members

    def _is_member_node(node: Any) -> bool:
        if not isinstance(node, dict):
            return False
        if not isinstance(node.get("name"), str) or not node["name"]:
            return False
        if not isinstance(node.get("id"), (str, int)):
            return False
        if node.get("__typename") in ("User", "GroupMember", "Member"):
            return True
        return any(k in node for k in ("is_admin", "isAdmin", "role", "membership_state"))

    for node in deep_find(data, _is_member_node):
        member_id = str(node.get("id") or "")
        if not member_id or member_id in seen:
            continue
        seen.add(member_id)
        is_admin = bool(node.get("is_admin") or node.get("isAdmin"))
        members.append(
            {
                "id": member_id,
                "name": node.get("name", ""),
                "username": node.get("username") or node.get("vanity") or "",
                "url": node.get("url") or node.get("profile_url") or "",
                "role": node.get("role") or ("admin" if is_admin else ""),
            }
        )
        if len(members) >= limit:
            break
    return members
