"""Facebook MCP Server main CLI application entry point."""

import asyncio
import json
import logging
import os
import sys

from curl_cffi.requests import AsyncSession as _AsyncSession

from facebook_mcp_server.authentication import clear_auth_state
from facebook_mcp_server.config import get_config
from facebook_mcp_server.drivers.browser import (
    close_browser,
    get_profile_dir,
    load_cookies,
    profile_exists,
)
from facebook_mcp_server.logging_config import configure_logging
from facebook_mcp_server.scraping import identity
from facebook_mcp_server.server import create_mcp_server
from facebook_mcp_server.session_state import (
    get_runtime_id,
    load_source_state,
    portable_cookie_path,
    source_state_path,
)
from facebook_mcp_server.setup import run_profile_creation
from facebook_mcp_server.tool_registry import TOOLS

logger = logging.getLogger(__name__)

# ── TOON output helpers (AXI §1) ───────────────────────────────────────────


def axi_error(msg: str, hint: str | None = None) -> None:
    """Print structured error to stdout (AXI §6) and exit with code 2."""
    print(f"error: {msg}")
    if hint:
        print(f"help: {hint}")
    sys.exit(2)


def _get_bin_path() -> str:
    """Get executable path with home dir collapsed to ~ (AXI §10)."""
    try:
        home = os.environ.get("HOME", "")
        exe = sys.argv[0] if sys.argv else "facebook-lyr"
        if home and exe.startswith(home):
            return exe.replace(home, "~", 1)
        return exe
    except Exception:
        return "facebook-lyr"


def get_version() -> str:
    try:
        from importlib.metadata import PackageNotFoundError, version

        for package_name in ("facebook-lyr", "facebook-mcp-server"):
            try:
                return version(package_name)
            except PackageNotFoundError:
                continue
    except Exception:  # noqa: S110 - best-effort version lookup
        pass
    try:
        import tomllib

        pyproject_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "pyproject.toml")
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)
            return data["project"]["version"]
    except Exception:
        return "unknown"


def toon_print_dict(data: dict, indent: int = 0) -> None:
    """Print dict in TOON format (AXI §1)."""
    prefix = "  " * indent
    for key, value in data.items():
        if isinstance(value, dict):
            print(f"{prefix}{key}:")
            toon_print_dict(value, indent + 1)
        elif isinstance(value, list):
            if value:
                print(f"{prefix}{key}[{len(value)}]:")
                for item in value:
                    if isinstance(item, dict):
                        toon_print_dict(item, indent + 1)
                    else:
                        print(f"{prefix}  {item}")
            else:
                print(f"{prefix}{key}: 0")
        else:
            print(f"{prefix}{key}: {value}")


def toon_print_array(data: list, schema: str, count: int | None = None) -> None:
    """Print array in TOON format with schema (AXI §1, §2, §4)."""
    if not data:
        print(f"{schema}: 0")
        return

    if count is not None:
        print(f"count: {len(data)} of {count} total")

    print(f"{schema}[{len(data)}]:")
    for item in data:
        if isinstance(item, dict):
            values = [str(item[key]) for key in item]
            print(f"  {','.join(values)}")
        else:
            print(f"  {item}")


# ── AXI §8: Content-first home view ───────────────────────────────────────


def show_home_view() -> None:
    """Show live state when no args provided (AXI §8)."""
    bin_path = _get_bin_path()
    version = get_version()
    profile_dir = get_profile_dir()

    has_session = False
    source_state = None
    try:
        source_state = load_source_state(profile_dir)
        has_session = source_state is not None and profile_exists(profile_dir)
    except Exception as e:
        print(f"warning: Session check failed: {e}", file=sys.stderr)

    print(f"bin: {bin_path}")
    print(f"version: {version}")
    print(
        "description: Facebook / Messenger MCP server — multi-account, pages, "
        "posts, media, comments, Messenger, and groups via browser-cookie scraping"
    )
    print()

    print("session:")
    if has_session and source_state:
        print("  status: valid")
        print(f"  runtime_id: {get_runtime_id()}")
        print(f"  source_runtime: {source_state.source_runtime_id}")
    else:
        print("  status: not_configured")
        print("  help: Run `facebook-lyr --login` to import browser cookies")
    print()

    tools_data = [{"name": name, "description": desc} for name, desc in TOOLS]
    toon_print_array(tools_data, "tools")
    print()

    print("help[4]:")
    print("  Run `facebook-lyr --tool-info <name>` for detailed parameters")
    print("  Run `facebook-lyr --list-tools` to see all tools")
    print("  Run `facebook-lyr --login` to import browser cookies")
    print("  Run `facebook-lyr` to start the MCP server")


def list_tools_and_exit() -> None:
    """List all available MCP tools and exit (AXI §8 content-first)."""
    tools_data = [{"name": name, "description": desc} for name, desc in TOOLS]
    toon_print_array(tools_data, "tools", count=len(TOOLS))
    print()
    print("help[2]:")
    print("  Run `facebook-lyr --tool-info <name>` for details")
    print("  Run `facebook-lyr` to start the MCP server")
    sys.exit(0)


def tool_info_and_exit(tool_name: str) -> None:
    """Show detailed info for a specific tool (AXI §9 contextual disclosure)."""
    mcp = create_mcp_server()
    tools = asyncio.run(mcp.list_tools())
    tool = next((t for t in tools if t.name == tool_name), None)
    if not tool:
        valid = sorted(t.name for t in tools)
        axi_error(f"Unknown tool: '{tool_name}'", f"Valid tools: {', '.join(valid)}")

    print(f"name: {tool.name}")
    print(f"description: {tool.description}")
    schema = tool.parameters or {}
    props = schema.get("properties", {})
    required = schema.get("required", [])
    print(f"parameters[{len(props)}]:")
    for name, prop in props.items():
        req = " (required)" if name in required else ""
        ptype = prop.get("type", "any")
        desc = (prop.get("description") or "").replace("\n", " ")
        print(f"  {name}: {ptype}{req} — {desc}")
    sys.exit(0)


# ── AXI §7: Session integrations ──────────────────────────────────────────


def _hook_command() -> str:
    """Build the SessionStart hook command (home view, forced via --hook).

    Hooks run with piped (non-TTY) stdin, which would otherwise start the MCP
    server; the explicit ``--hook`` flag forces the content-first home view so
    the session shows live Facebook state instead of blocking on stdio.
    """
    bin_path = _get_bin_path()
    base = bin_path if bin_path.startswith("~") else sys.argv[0]
    return f"{base} --hook"


def install_session_hook_and_exit() -> None:
    """Install session hooks for Claude Code/Codex (AXI §7)."""
    from pathlib import Path

    home_dir = Path.home()
    hook_command = _hook_command()

    claude_settings = home_dir / ".claude" / "settings.json"
    try:
        if claude_settings.exists():
            with open(claude_settings, "r") as f:
                settings = json.load(f)
            if "hooks" not in settings:
                settings["hooks"] = {}
            settings["hooks"]["SessionStart"] = hook_command
            with open(claude_settings, "w") as f:
                json.dump(settings, f, indent=2)
            print("status: success")
            print("target: claude_code")
            print(f"hook: SessionStart -> {hook_command}")
            print("help: Session will now show Facebook MCP state on startup")
        else:
            print("warning: Claude Code settings not found")
            print("help: Install Claude Code to enable session hooks")
    except Exception as e:
        print(f"error: Failed to install Claude Code hook: {e}")
        sys.exit(1)

    codex_hooks = home_dir / ".codex" / "hooks.json"
    try:
        if codex_hooks.exists():
            with open(codex_hooks, "r") as f:
                hooks = json.load(f)
            hooks["SessionStart"] = hook_command
            with open(codex_hooks, "w") as f:
                json.dump(hooks, f, indent=2)
            print("status: success")
            print("target: codex")
            print(f"hook: SessionStart -> {hook_command}")
        else:
            print("warning: Codex hooks not found")
            print("help: Install Codex to enable session hooks")
    except Exception as e:
        print(f"error: Failed to install Codex hook: {e}")
        sys.exit(1)

    sys.exit(0)


def install_agent_skill_and_exit() -> None:
    """Create installable agent skill from home view (AXI §7)."""
    from pathlib import Path

    skill_dir = Path.home() / ".claude" / "skills" / "facebook-mcp"
    skill_dir.mkdir(parents=True, exist_ok=True)

    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(_SKILL_TEMPLATE)

    print("status: success")
    print(f"skill_path: {skill_file}")
    print("help: Agent skill installed - will load automatically on Facebook tasks")
    sys.exit(0)


_SKILL_TEMPLATE = """# Facebook / Messenger MCP Server

description: Facebook Pages and Messenger automation via browser-cookie scraping — multi-account switching, posting (text/photo/video/carousel), messaging, comments, and groups
triggers:
  - "facebook post"
  - "facebook page"
  - "facebook messenger"
  - "facebook comment"
  - "messenger message"
  - "social media posting"
  - "content creation"
  - "facebook account switch"
  - "facebook pages admin"

## Overview
The Facebook MCP Server gives agents cookie-authenticated access to:
- Multi-account: store multiple identities (users + Pages), switch the active
  identity, discover switchable Pages, import from browsers
- Profiles & Pages: profile info, timeline posts, page/post search
- Posts: create text/photo/video/carousel posts, read post details and comments
- Messenger: conversations, message reading/sending, typing, seen markers
- Groups: group info, group feeds, group search

Write tools (post/comment/message) are confirm-gated and fail honestly when
Facebook has decommissioned the cookie-based transport for an account — the
validated attempt and its result are recorded per identity.

## Quick Start
```bash
facebook-lyr                 # Home view with live session state
facebook-lyr --status        # Check cookie session validity
facebook-lyr --login         # Import browser cookies interactively
facebook-lyr --list-tools    # List available tools
facebook-lyr --tool-info <tool>  # Tool parameters
```

## Multi-Account
```bash
facebook-lyr list_accounts
facebook-lyr list_switchable_accounts   # Pages/profiles this session can switch to
facebook-lyr add_account --account_id <id> --name "Page" --cookies '{"c_user":"..","xs":".."}'
facebook-lyr import_account_from_browser --account_id <id> --name "Page" --browser zen
facebook-lyr switch_active_account --account_id <id>
facebook-lyr get_active_account_info
facebook-lyr get_account_posting_status --account_id <id>
```
Every read/write tool acts as the active identity; pass `--account_id` to
override per call.

## Direct CLI Tool Execution
```bash
facebook-lyr get_me
facebook-lyr get_account_status
facebook-lyr get_page_info --username cocacola
facebook-lyr get_page_posts --username cocacola --max_posts 10
facebook-lyr get_conversations
facebook-lyr send_message --thread_id <tid> --message "Hi" --confirm true
facebook-lyr create_post --status "Hello" --confirm true
facebook-lyr create_photo_post --source /path/img.jpg --message "caption" --confirm true
# Output as JSON
facebook-lyr get_page_posts --username cocacola --max_posts 5 --json
```

## Workflows
1. Discover: `get_me` -> `list_switchable_accounts` -> `get_page_posts` / `get_conversations`
2. Publish: `create_post --status "..." --confirm true` (or photo/video/carousel)
3. Engage: `get_post_comments` -> `comment_on_post --post_id ... --text ... --confirm true`
4. Messenger: `get_conversations` -> `get_conversation_messages` -> `send_message`
"""


# ── Profile management helpers ─────────────────────────────────────────────


def clear_profile_and_exit() -> None:
    """Clear Facebook profile and exit (AXI §6 idempotent mutations)."""
    config = get_config()
    configure_logging(
        log_level=config.server.log_level,
        json_format=not config.is_interactive and config.server.log_level != "DEBUG",
    )

    profile_dir = get_profile_dir()
    if not (
        profile_exists(profile_dir)
        or portable_cookie_path(profile_dir).exists()
        or source_state_path(profile_dir).exists()
    ):
        print("status: nothing_to_clear")
        print("message: No authentication state found")
        sys.exit(0)

    if clear_auth_state(profile_dir):
        print("status: success")
        print("message: Authentication state cleared")
    else:
        print("error: Failed to clear authentication state")
        sys.exit(1)
    sys.exit(0)


def get_profile_and_exit() -> None:
    """Create profile interactively (cookie import) and exit."""
    config = get_config()
    configure_logging(
        log_level=config.server.log_level,
        json_format=not config.is_interactive and config.server.log_level != "DEBUG",
    )

    profile_dir = get_profile_dir()
    browser_id = config.cookie.preferred_browser
    success = run_profile_creation(str(profile_dir), browser_id=browser_id)
    sys.exit(0 if success else 1)


def profile_info_and_exit() -> None:
    """Check profile validity and display info, then exit."""
    config = get_config()
    configure_logging(
        log_level=config.server.log_level,
        json_format=not config.is_interactive and config.server.log_level != "DEBUG",
    )

    profile_dir = get_profile_dir()
    cookies_path = portable_cookie_path(profile_dir)
    source_state = load_source_state(profile_dir)
    if not source_state or not profile_exists(profile_dir) or not cookies_path.exists():
        print("error: No valid source session")
        print(f"profile_dir: {profile_dir}")
        print("help: Run with --login to import browser cookies")
        sys.exit(1)

    print(f"profile_dir: {profile_dir}")
    print(f"runtime_id: {get_runtime_id()}")
    if source_state:
        print(f"source_runtime: {source_state.source_runtime_id}")
        print(f"login_generation: {source_state.login_generation}")

    valid = asyncio.run(_check_session_api())

    if valid:
        print("status: valid")
        sys.exit(0)

    print("status: expired")
    print("help: Run with --login to re-authenticate")
    sys.exit(1)


async def _check_session_api() -> bool:
    """Check Facebook session validity by fetching the logged-in homepage."""
    try:
        cookies = load_cookies()
        if not cookies:
            return False

        headers = {
            "User-Agent": identity.USER_AGENT,
        }
        headers.update(identity.client_hints())
        async with _AsyncSession(
            impersonate=identity.IMPERSONATE,
            cookies=cookies,
            headers=headers,
            timeout=15,
            allow_redirects=True,
        ) as client:
            resp = await client.get("https://www.facebook.com/")
            if resp.status_code == 429:
                logger.warning("Rate limited during session check")
                return False
            if resp.status_code != 200:
                return False
            from facebook_mcp_server.scraping.parsers import (
                detect_login_wall,
                extract_fb_dtsg,
            )

            if detect_login_wall(resp.text, str(resp.url or "")):
                return False
            return extract_fb_dtsg(resp.text) is not None
    except Exception as e:
        logger.warning(f"Session check failed: {e}")
        return False


# ── Help output ────────────────────────────────────────────────────────────


def show_help() -> None:
    """Show usage information (AXI §10)."""
    version = get_version()
    print(f"facebook-lyr v{version}")
    print("Facebook / Messenger MCP server via browser-cookie scraping")
    print()
    print("Usage:")
    print("  facebook-lyr                    Show home view with live state")
    print("  facebook-lyr <tool> [args]      Call a tool directly (see examples)")
    print("  facebook-lyr --list-tools       List all available MCP tools")
    print("  facebook-lyr --tool-info <name> Show details for a specific tool")
    print("  facebook-lyr --login            Import browser cookies (facebook.com)")
    print("  facebook-lyr --logout           Clear stored session")
    print("  facebook-lyr --status           Check authentication status")
    print("  facebook-lyr --install-hook     Install session hooks (Claude Code/Codex)")
    print("  facebook-lyr --install-skill    Install agent skill for auto-discovery")
    print("  facebook-lyr --json             Render tool results as JSON")
    print()
    print("Examples:")
    print("  facebook-lyr get_page_posts --username cocacola --max_posts 10")
    print("  facebook-lyr get_conversations")
    print("  facebook-lyr send_message --thread_id <tid> --message 'Hi' --confirm true")
    print()
    print("Environment:")
    print("  FACEBOOK_COOKIES           JSON object with c_user + xs (headless agents)")
    print("  FACEBOOK_PREFERRED_BROWSER Browser to auto-import from (chrome, brave, ...)")
    print("  USER_DATA_DIR              Profile directory (default ~/.facebook-lyr/profile)")


# ── Main ───────────────────────────────────────────────────────────────────


def exit_gracefully(exit_code: int = 0) -> None:
    try:
        asyncio.run(close_browser())
    except Exception:  # noqa: S110 - best-effort teardown
        pass
    sys.exit(exit_code)


def main() -> None:
    """Main application entry point."""
    config = get_config()

    configure_logging(
        log_level=config.server.log_level,
        json_format=not config.is_interactive and config.server.log_level != "DEBUG",
    )

    # If FACEBOOK_COOKIES is set, materialize the session profile before any
    # status/tool path checks it (mirrors the MCP server lifespan bootstrap).
    from facebook_mcp_server.bootstrap import initialize_bootstrap

    initialize_bootstrap()

    raw_args = sys.argv[1:]

    if "--help" in raw_args or "-h" in raw_args:
        show_help()
        sys.exit(0)

    if "--list-tools" in raw_args:
        list_tools_and_exit()

    if "--tool-info" in raw_args:
        idx = raw_args.index("--tool-info")
        if idx + 1 >= len(raw_args):
            axi_error(
                "--tool-info requires a tool name",
                "Usage: facebook-lyr --tool-info <tool-name>",
            )
        tool_info_and_exit(raw_args[idx + 1])

    if "--install-hook" in raw_args:
        install_session_hook_and_exit()

    if "--install-skill" in raw_args:
        install_agent_skill_and_exit()

    # Session hooks run with piped (non-TTY) stdin; --hook forces the home
    # view so SessionStart shows live state instead of starting the server.
    if config.server.hook:
        show_home_view()
        sys.exit(0)

    if config.server.logout:
        clear_profile_and_exit()

    if config.server.login:
        get_profile_and_exit()

    if config.server.status:
        profile_info_and_exit()

    if len(raw_args) == 0:
        if config.is_interactive:
            # Human terminal: content-first home view (AXI §8).
            show_home_view()
            sys.exit(0)
        # Non-interactive stdin (MCP client spawning the server, Docker
        # pipe, CI): fall through and start the JSON-RPC server instead.
        logger.debug("Non-interactive stdin with no args — starting MCP server")

    try:
        transport = config.server.transport

        mcp = create_mcp_server()

        if transport == "streamable-http":
            mcp.run(
                transport=transport,
                host=config.server.host,
                port=config.server.port,
                path=config.server.path,
            )
        else:
            mcp.run(transport=transport)
    except KeyboardInterrupt:
        exit_gracefully(0)
    except Exception as e:
        print(f"error: Server runtime error: {e}")
        exit_gracefully(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        exit_gracefully(0)
    except Exception as e:
        print(f"error: Error running MCP server: {e}")
        exit_gracefully(1)
