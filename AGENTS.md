# AGENTS.md — guidance for AI agents working on facebook-lyr

facebook-lyr is a cookie-scraping MCP server for Facebook / Messenger /
Threads, mirroring instagram-lyr's architecture exactly.

## Layout

```
facebook_mcp_server/
├── core/                scraping exception hierarchy (AuthenticationError, RateLimitError, ...)
├── config/              schema (CookieConfig) + loaders (CLI > env > defaults)
├── session_state.py     SourceState/RuntimeState + portable cookies.json (0600)
├── cookie_import.py     browser registry + SQLite cookie extraction (facebook.com)
├── setup.py             interactive profile creation (--login)
├── drivers/browser.py   load_cookies / validate_session (curl_cffi)
├── scraping/
│   ├── identity.py      shared UA + Chrome TLS impersonation fingerprint
│   ├── parsers.py       fb_dtsg extraction, login-wall detection, embedded-JSON deep-scan
│   └── api_client.py    FacebookScrapeClient — all web surfaces
├── authentication.py    source-session checks
├── bootstrap.py         auth gating + FACEBOOK_COOKIES env bootstrap
├── dependencies.py      get_ready_client + cookie write-back sink
├── error_handler.py     ToolError mapping + issue diagnostics
├── tools/               tool_guard + registration modules
├── server.py            FastMCP server + sequential middleware
├── cli_main.py          AXI CLI (home view, --login, --status, direct tools)
└── tool_registry.py     tool list + run_tool_direct
```

## Rules

1. **One fingerprint bundle.** Every request must use the same UA + TLS
   impersonation + Client Hints (`scraping/identity.py`). Presenting the same
   session under two pairings is a bot-detection tell.
2. **fb_dtsg is a CSRF token.** It is fetched from the logged-in homepage and
   cached on the client. Ajax write endpoints require it.
3. **Never hardcode page-structure paths.** Facebook's embedded JSON changes
   constantly — use the deep-scan predicates in `parsers.py`, not fixed key
   paths. Add fixture-backed tests when adding a parser.
4. **Login walls must become AuthenticationError.** Every HTML page fetch
   checks `detect_login_wall`; ajax endpoints rely on 401/403 instead.
5. **Write tools are gated.** `confirm=true` + write pacing. Keep the gap
   human-like; agent loops must not burst writes.
6. **Auth artifacts are 0600.** Cookies are credentials — never log them, never
   return them in tool output.
7. **No new Graph-API code.** This project deliberately uses cookie scraping,
   like instagram-lyr. Don't re-add `graph.facebook.com` calls.

## Testing

- `uv run pytest` — 140 tests, all offline (fake curl_cffi session in
  `tests/conftest.py` with route tables).
- `uv run ruff check . && uv run ruff format --check .`
- New parsers need fixture-HTML tests (`tests/test_parsers.py`).
- Session-flow tests use the `fake_session` fixture — patch
  `scraping.api_client._AsyncSession`.

## Environment

- Tests redirect the profile dir via `USER_DATA_DIR`.
- Headless auth uses `FACEBOOK_COOKIES` (JSON with `c_user` + `xs`).
- Never test against real cookies; keep the suite hermetic.
