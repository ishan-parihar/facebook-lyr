
<!-- T2I HERO SPEC — Subject: a Facebook data engine — a blue profile/fan-page frame feeding posts, comments, and groups through a pipeline into clean structured data for agents. Composition: page frame → extraction → JSON cards. Palette: facebook blue #1877f2 → dark slate → clean white. Style: dark flat vector, thumb icon motif, no text. 16:9. -->
<p align="center">
  <img src="https://img.shields.io/badge/python-3.11+-2b6cb0?style=flat&logo=python&logoColor=white" alt="Python 3.11+">
[![CI](https://github.com/ishan-parihar/facebook-lyr/actions/workflows/ci.yml/badge.svg)](https://github.com/ishan-parihar/facebook-lyr/actions/workflows/ci.yml)
  <img src="https://img.shields.io/badge/uv-0.6%2B-7c3aed?style=flat&logo=astral&logoColor=white" alt="uv">
  <img src="https://img.shields.io/badge/license-MIT-059669?style=flat" alt="MIT">
</p>

<h1 align="center">facebook-lyr</h1>

<p align="center">
  <strong>A Facebook / Messenger MCP server for AI agents</strong> — profile &amp; page reads, post and
  comment search, groups, Messenger, notifications, events, and multi-account + Page switching, all through
  the <strong>same session your browser already has</strong>. No Graph API app, no tokens, no browser automation.
</p>

<p align="center">
  <code>curl -fsSL https://raw.githubusercontent.com/ishan-parihar/facebook-lyr/main/install.sh | bash</code>
</p>

---

## What it does

- **Read & discover** — profiles, Pages, timeline posts, post details & comments, groups (+ members), people/page/group/post search, Messenger conversations & messages, **notifications**, and **upcoming events**.
- **Write & engage** — text / photo / video / carousel posts, **delete your own posts**, **save & unsave** posts, comment on posts, and full Messenger operations (send, **delete/unsend**, typing indicator, mark-seen).
- **Multi-account** — store multiple identities (your user, Pages you administer, linked profiles), each with its own cookie set; switch the active identity any time, discover switchable accounts, import cookies per identity, and enforce per-identity posting limits + cooldowns + attempt history.
- **Agent-first CLI** — AXI-compliant, TOON output, session hooks for Claude Code / Codex, and a self-installing agent skill.

Modeled directly on [`instagram-lyr`](https://github.com/ishan-parihar/instagram-lyr) — the same architecture, CLI conventions, and browser-cookie authentication flow. Threads lives in a separate project (`threads-lyr`).

## Why cookie scraping?

The official Graph API requires a Meta developer app, app review, and page-scoped tokens — and it cannot see anything your app wasn't granted. Cookie scraping reuses the **exact session your browser has**, so the agent reads and acts on Facebook the way *you* would: full timelines, groups, Messenger inboxes, notifications, and events.

- `curl_cffi` with Chrome TLS impersonation defeats JA3 fingerprint detection.
- The `fb_dtsg` CSRF token is extracted from the logged-in homepage and auto-refreshed on rotation.
- Messenger uses the legacy **Mercury** ajax endpoints (`threadlist_info.php`, `thread_info.php`, `send_messages.php`, `delete_messages.php`) where they still exist.
- Feeds, profiles, notifications, events, and search are parsed from Facebook's embedded JSON with a resilient recursive deep-scan — no fragile selectors.
- Account discovery reads the relay data on `/accounts/` to list every Page / profile the session can switch to.

## Quick install

**One-line installation (clones to `~/projects/facebook-lyr`):**

```bash
curl -fsSL https://raw.githubusercontent.com/ishan-parihar/facebook-lyr/main/install.sh | bash
```

**Or install from PyPI with uv (no clone):**

```bash
uv tool install facebook-lyr
```

**Or in any MCP client — just point it at the package:**

```json
{
  "mcpServers": {
    "facebook": {
      "command": "uvx",
      "args": ["facebook-lyr"]
    }
  }
}
```

## Quick start

```bash
uv sync                        # install dependencies (repo install)
facebook-lyr --login           # import cookies from your browser (Brave, Chrome, Edge, Firefox, Zen, ...)
facebook-lyr --status          # "status: valid" when the session works
facebook-lyr                   # start the MCP server (stdio)
```

**Headless agents** (no interactive step): export the session directly —

```bash
export FACEBOOK_COOKIES='{"c_user":"12345","xs":"abc...","fr":"...","datr":"..."}'
facebook-lyr --status
```

> Cookies are stored at `~/.facebook-lyr/profile/cookies.json` (mode `0600`) and never leave your machine.

## Tool surface (41 tools)

### Read & discover

| Tool | What it gives you |
|---|---|
| `get_me` / `get_account_status` | Authenticated identity + session validity |
| `get_user_posts` / `get_page_posts` | Recent timeline posts for a user or Page |
| `get_page_info` / `get_profile`-style info | Profile / Page summary (bio, follower counts) |
| `get_post_details` / `get_post_comments` | Single post + its comments |
| `search_posts` / `search_people` / `search_pages` / `search_groups` | Keyword discovery across Facebook |
| `get_group` / `get_group_posts` / `get_group_members` | Group metadata, feed, and member list |
| `get_conversations` / `get_conversation_messages` | Messenger inbox + thread bodies |
| `get_notifications` | New alerts (messages, tags, follows, page activity) |
| `get_events` | Upcoming events for the identity |
| `get_account_posting_status` | Posting limits + attempt history for an identity |

### Write & engage

Every write tool is **confirm-gated** (`confirm=true`), respects the identity's daily cap + cooldown, and records the attempt in the identity's posting history.

| Tool | Notes |
|---|---|
| `create_post` | Text post (≤ 5000 chars) |
| `create_photo_post` | Image (file/URL), normalized to feed spec |
| `create_video_post` | Video (file/URL) — best-effort |
| `create_carousel_post` | 2–10 image carousel |
| `delete_post` | Delete one of your own posts (`delete.php`) |
| `save_post` / `unsave_post` | Bookmark / un-bookmark a post |
| `comment_on_post` | Comment on a post |
| `send_message` / `delete_message` | Send, or delete/unsend, a Messenger message |
| `send_typing_indicator` / `mark_conversation_seen` | Presence writes (no quota) |

### Accounts & identity

| Tool | What it gives you |
|---|---|
| `list_accounts` / `get_active_account_info` | Stored identities + which is active |
| `list_switchable_accounts` | Every Page/profile the session can switch to |
| `add_account` / `remove_account` / `update_account_cookies` | Manage per-identity cookie stores |
| `import_account_from_browser` | Import an identity's cookies from a browser |
| `switch_active_account` / `set_active_page` | Change the identity every tool acts as |
| `close_session` | Release active clients |

## Authentication

- **Interactive:** `facebook-lyr --login` — imports `c_user`, `xs`, `fr`, `datr`, … from your browser's cookie store (Brave, Chrome, Edge, Firefox, Zen, Chromium, Opera, Arc, Vivaldi, LibreWolf, Waterfox, Floorp, and more).
- **Headless:** `FACEBOOK_COOKIES` env var (JSON object with at least `c_user` and `xs`).
- If the session expires (login wall / 403), tools auto-trigger re-login and tell you to run `--login`.

## Multi-account (users + Pages)

Store multiple identities — your user account, Pages you administer, linked profiles — each with its own cookie set under `~/.facebook-lyr/accounts/<id>/`:

```bash
facebook-lyr import_account_from_browser --account_id 100064606727322 --name "My Page" --browser zen
facebook-lyr switch_active_account --account_id 100064606727322
facebook-lyr get_active_account_info
```

Cookie rotations (xs/fr refreshes) are written back to the owning identity's store automatically, so stored jars stay in sync with the live session. Posting limits, cooldowns, and attempt history are tracked per identity.

## Honest limitations

Facebook has been decommissioning its cookie-era write endpoints. Where a write path is dead, the tool **fails loudly and honestly** instead of faking success:

| Capability | Status |
|---|---|
| Timeline publishing (text/photo/video/carousel) via **Graph API** | 🟢 **working** — requires a page-scoped token (see below); the cookie path is decommissioned |
| Comment on posts / delete own posts | 🟢 working (Graph API for comments, cookie `delete.php` for delete) |
| Save / unsave posts, notifications, events, group members | 🟢 working (legacy endpoints still alive) |
| Messenger text send / delete / typing / seen | 🔴 **decommissioned** — `send_messages.php` returns 404 (verified live); the read side (`get_conversations`) still works |
| React to post/comment, edit post, follow, friend requests, Messenger media/reactions | 🔴 endpoint removed (404) — not possible with cookies or the current token |
| Post via cookies for migrated accounts | 🔴 returns a clear "official API required" error |

**Graph API writes** need a page-scoped access token, deployed as a sidecar `~/.facebook-lyr/.env`:

```bash
FACEBOOK_GRAPH_API_TOKEN=...
FACEBOOK_GRAPH_API_PAGE_ID=...
```

When the token is present, `facebook-lyr` automatically routes timeline publishing + comments through the Graph API; without it, those writes fail honestly with the "official API required" error. Read tools and the messenger read side are unaffected.

## CLI reference

```bash
facebook-lyr --help                 # usage
facebook-lyr --list-tools           # all 41 tools
facebook-lyr --tool-info <tool>     # params for one tool
facebook-lyr <tool> --param value   # call a tool directly (TOON output)
facebook-lyr --login | --status | --logout
facebook-lyr --install-hook         # session hooks for Claude Code / Codex
facebook-lyr --install-skill        # agent skill for auto-discovery
```

## Development

```bash
uv sync --extra dev
uv run pytest        # 259 tests: parsers, write surface, limits, Tier-1 tools
uv run ruff check .
```

## License

MIT — see [LICENSE](LICENSE).
