# facebook-lyr — Production-Readiness Audit Report

**Date:** 2026-08-04
**Scope:** Full read/write tool surface vs. what an AI agent needs for production-grade Facebook UX operation (profile, pages, groups, messenger, notifications, events) via cookie-based scraping.
**Method:** Live inventory of the running MCP server (34 tools) + **safe endpoint probes** executed against the real session (imported Zen cookies) — all mutation probes used bogus object IDs, so nothing real was changed.

---

## 1. Verdict

> **NOT full production-grade yet.** The read + account/multi-account + messenger-text surface is production-strong (34 tools, 223 tests, live-verified), **but**:
> 1. All **timeline publishing is honest-blocked** (Facebook decommissioned the cookie composer — verified by probe), and
> 2. Several **feasible** capabilities (delete post, save/bookmark, notifications, events, group members, message management) are **not implemented**, and
> 3. A cluster of desired write capabilities (reactions, follow, edit, friend requests, messenger media, message reactions) are **decommissioned at the endpoint level** — impossible with cookies, confirmed by probe.

Per the owner's instruction, work **stops here** and this report is the gate. Publishing/commit/PyPI/VPS deployment is deferred until the owner decides the path forward.

---

## 2. Current Surface (34 tools, live)

### Read / discovery (19)
`get_me`, `get_account_status`, `get_account_posting_status`, `get_active_account_info`, `get_conversations`, `get_conversation_messages`, `get_group`, `get_group_posts`, `get_page_info`, `get_page_posts`, `get_post_comments`, `get_post_details`, `get_user_posts`, `search_groups`, `search_pages`, `search_people`, `search_posts`, `list_accounts`, `list_switchable_accounts`

### Account / identity (multi-account + page switching)
`add_account`, `remove_account`, `update_account_cookies`, `import_account_from_browser`, `switch_active_account`, `set_active_page`, `close_session`

### Write / engagement (8, all confirm-gated, limit-checked, attempt-recorded)
`create_post`, `create_photo_post`, `create_video_post`, `create_carousel_post`, `comment_on_post`, `send_message`, `send_typing_indicator`, `mark_conversation_seen`

---

## 3. Live Probe Evidence (real session, bogus IDs — no side effects)

### 🔴 Decommissioned (HTTP 404) — impossible with cookies
| Capability | Endpoint probed | Result |
|---|---|---|
| Timeline text post | `updatestatus.php` | 404 (verified earlier) |
| Photo/video upload | composer `attachment/create/upload/` | 404 |
| Group post | `/groups/<id>/composer/` | 404 |
| **Comment on post** | `/ufi/comment/` | **404** |
| React to post | `/ufi/reaction/profile/` | 404 |
| React to comment | `/ufi/reaction/comment/` | 404 |
| View reactions (read) | `/ufi/reaction/profile/browser/fetch/` | 404 |
| Edit post | `/ajax/ufi/edit_post.php` | 404 |
| Follow / unfollow user | `/ajax/follow/` | 404 |
| Send friend request | `/ajax/requests/` | 404 |
| Messenger photo/media | `upload.facebook.com/ajax/messaging/attachments/` | 404 |
| Message reaction | `/ajax/messaging/react/` | 404 |

### 🟢 Alive (200) — feasible to implement now
| Capability | Endpoint probed | Result |
|---|---|---|
| **Delete own post** | `delete.php?post_id=` | 200 ✅ |
| **Save / bookmark post** | `/ajax/saved/` | 200 ✅ |
| **Notifications (read)** | `/notifications/` | 200 ✅ |
| **Events (read)** | `/events/calendar/` | 200 ✅ |
| **Groups (read)** | `/groups/` | 200 ✅ |
| Messenger text send | Mercury ajax | ✅ (implemented, honest errors) |
| Typing indicator / seen | Mercury ajax | ✅ (implemented) |

### 🟡 Undetermined
- Stories upload (`/stories/composer/` returns 200 page HTML; the actual upload action was **not** verified — needs a guarded experiment).

---

## 4. Gap Analysis — missing capabilities for production-grade UX

### Tier 1 — Feasible now, should be implemented (probe-confirmed alive)
| # | Tool | Feasibility | Notes |
|---|---|---|---|
| 1 | `delete_post(post_id, confirm)` | ✅ delete.php alive | Destructive, confirm-gated, attempt-recorded like the rest |
| 2 | `save_post(post_id)` / `unsave_post(post_id)` | ✅ /ajax/saved/ alive | Bookmark/collection management |
| 3 | `get_notifications(limit)` | ✅ /notifications/ alive | Parse the notifications page / ajax |
| 4 | `get_events(limit)` | ✅ /events/calendar/ alive | Read event listings |
| 5 | `get_group_members(group_id)` | ✅ groups alive | Member list read |
| 6 | `delete_message(thread_id, message_id)` | 🟡 Mercury family | Likely feasible (fbchat-era endpoint); needs guarded probe |
| 7 | `edit_message(thread_id, message_id, text)` | 🟡 Mercury family | Same — guarded probe required |
| 8 | `search_conversations(query)` | 🟡 Mercury/graphql | For agent-driven message triage |

### Tier 2 — Decommissioned at endpoint level, impossible with cookies (document, don't fake)
Timeline publishing (all media types), commenting, reactions, edit post, follow/unfollow, friend requests, messenger media, message reactions, group posting, page posting, event creation, page/group creation. These require the **official Graph API** (Page/App tokens) or GraphQL doc-IDs that are lazily rotated and not statically scrapable. The current honest-error posture is the correct production behavior for these.

### Tier 3 — Out of scope by design
- Ads manager, payments, marketplace transactions, identity verification.

---

## 5. Readiness of the pipeline that WAS about to run

| Item | Status |
|---|---|
| Tool surface | 34 tools, live-verified, confirm gates + limits + recording |
| Tests | **223 pass** (facebook-lyr), **31 pass** (threads-lyr), ruff clean, formatted |
| Live session | Valid (`~/.facebook-lyr`), imported from Zen, session survives write attempts |
| git | facebook-lyr is **not yet a git repo** (needs `gh repo create` + init) |
| gh CLI | ✅ Authenticated as `ishan-parihar` (repo + workflow scopes) |
| Packaging | pyproject.toml exists (`facebook-lyr` 0.2.0) — needs instagram-parity install.sh, README, PyPI (test) publish |
| VPS | ✅ Reachable (`nerd@racknerd` via sshpass), has `uv` + python3 |

---

## 6. Recommended Path (owner's decision required)

**Option A — Close Tier 1 gaps first, then package + deploy (recommended).**
~1 focused sprint: implement delete/save/notifications/events/group-members (+ guarded probes for message mgmt), expand the test matrix, then commit → push → PyPI (test) → VPS install + cookie port.

**Option B — Ship current surface now.**
Commit/push/PyPI/VPS immediately; Tier 1 gaps land as v0.3.0. Faster time-to-value; agents get honest "decommissioned" errors on publishing until then.

**Option C — Hybrid.** Ship now, but queue Tier 1 as the first v0.3.0 milestone.

## Resolution (2026-08-04)

Following the owner's decision **"Close gaps, then ship"**, all Tier-1 gaps were implemented, tested, and shipped:

| Tier-1 gap | Status |
|---|---|
| `delete_post` | ✅ implemented + live-verified (delete.php) |
| `save_post` / `unsave_post` | ✅ implemented (best-effort, honest) |
| `get_notifications` | ✅ implemented + live-verified (real unread data) |
| `get_events` | ✅ implemented + live-verified (real events) |
| `get_group_members` | ✅ implemented (legacy ajax; honest empty on private groups) |
| `delete_message` | ✅ implemented + live-verified (Mercury delete_messages) |
| edit_message / search_conversations | ❌ endpoint-level 404 — documented as Tier 2 (impossible with cookies) |

**Final state shipped:** 41 tools (34 → 41), **261 tests** (223 → 261), ruff clean, formatted.
Published to **Test PyPI** (facebook-lyr 0.3.1), pushed to **GitHub**
(ishan-parihar/facebook-lyr, public), and deployed + cookie-ported to the
**VPS** (nerd@racknerd) — session valid, live reads confirmed there.

Remaining honest limitations (Tier 2, verified 404): timeline/group publishing,
commenting, reactions, edit post, follow, friend requests, messenger media,
message reactions — these require the official Graph API. The tools surface
clean decommissioned errors rather than faking success.

---

*Prepared by Buffy · evidence-grounded · nothing real was mutated during probing*
