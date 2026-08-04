# CLAUDE.md — development guide

## Project

Cookie-scraping MCP server for Facebook / Messenger / Threads, mirroring
`instagram-lyr`. Auth = browser cookies (`c_user` + `xs`), transport =
`curl_cffi` Chrome impersonation against `www.facebook.com` private-web
endpoints + anonymous `threads.net`.

## File map

| File | Purpose |
| --- | --- |
| `scraping/identity.py` | Shared UA / `chrome131` impersonation / client hints |
| `scraping/parsers.py` | fb_dtsg extraction, login-wall detection, embedded-JSON deep-scan |
| `scraping/api_client.py` | `FacebookScrapeClient` — profiles, posts, messenger, comments, groups, composer uploads, Threads |
| `cookie_import.py` | Browser registry + SQLite cookie extraction |
| `drivers/browser.py` | `load_cookies` / `validate_session` |
| `session_state.py` | Source/runtime state + portable `cookies.json` (0600) |
| `bootstrap.py` | Auth gating + `FACEBOOK_COOKIES` env bootstrap + login flow |
| `dependencies.py` | `get_ready_client` + cookie write-back sink |
| `error_handler.py` | Exception → `ToolError` mapping + diagnostics |
| `tools/` | `tool_guard` + 8 modules registering 25 tools |
| `cli_main.py` | AXI CLI (home view, `--login`, `--status`, direct tool calls) |
| `tool_registry.py` | Tool list for direct CLI execution |

## Key endpoints

- Homepage (fb_dtsg): `GET https://www.facebook.com/`
- Profiles/pages/groups: `GET /{username}` / `GET /groups/{id}` (embedded JSON)
- Messenger: `GET /ajax/mercury/threadlist_info.php`,
  `GET /ajax/mercury/thread_info.php`,
  `POST /ajax/mercury/send_messages.php`
- Writes: `POST /ajax/updatestatus.php` (posts), `POST /ajax/ufi/ufi_composer.php` (comments),
  `POST /ajax/composer/attachment/create/upload/` (photos, best-effort)
- Threads: `GET https://www.threads.net/@{username}` (anonymous)

## Rules

1. Keep the fingerprint bundle coherent — never mix UA/TLS pairings for one session.
2. Extend parsers via deep-scan predicates, not hardcoded key paths.
3. All write tools: `confirm=true` gate + write pacing.
4. Cookies are credentials — 0600 on disk, never logged or returned.
5. Don't add Graph API / token code back; this is a cookie-scraping project.

## Commands

```bash
uv run pytest -q            # 140 tests, fully offline
uv run ruff check .         # lint (clean)
uv run ruff format .        # format
uv run facebook-lyr --list-tools
uv run facebook-lyr --status   # uses real cookies if configured
```

## Auth checks

- `FACEBOOK_COOKIES` env JSON: must include `c_user` and `xs`.
- Interactive: `uv run facebook-lyr --login` (browser cookie import).
- Validation: fetch homepage, require fb_dtsg token + no login wall.
