"""
Tool registry for direct CLI execution.

This module is separate from cli_main to avoid circular imports when
early-intercepting tool names in __main__.py.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any


# Mock Context for direct CLI execution
class MockContext:
    """Mock FastMCP Context for direct CLI tool execution."""

    def __init__(self) -> None:
        self._progress = []

    async def report_progress(
        self, message: str, progress: float = 0.0, total: float = 100.0
    ) -> None:
        """Mock progress reporting."""
        self._progress.append((message, progress, total))


TOOLS = [
    # Account
    ("get_me", "Get authenticated user identity"),
    ("get_account_status", "Check cookie session validity and active Page"),
    ("set_active_page", "Record the active Page for context"),
    # Multi-account
    ("list_accounts", "List stored Facebook identities"),
    ("list_switchable_accounts", "Discover identities the session can switch to"),
    ("add_account", "Store a new Facebook identity's cookies"),
    ("remove_account", "Remove a stored Facebook identity"),
    ("switch_active_account", "Switch the active identity all tools act as"),
    ("get_active_account_info", "Get the currently active identity"),
    ("update_account_cookies", "Refresh a stored identity's cookies"),
    ("import_account_from_browser", "Import an identity's cookies from a browser"),
    ("get_account_posting_status", "Check an identity's posting limits and history"),
    # Pages
    ("get_page_info", "Get Page/profile details by username"),
    ("get_page_posts", "Get recent Page timeline posts"),
    ("search_pages", "Search public Pages"),
    # Posts
    ("get_user_posts", "Get recent user/Page timeline posts"),
    ("get_post_details", "Get a single post's details"),
    ("search_posts", "Search public posts"),
    # Write tools (confirm required)
    ("create_post", "Publish a text post (confirm required)"),
    ("create_photo_post", "Upload + publish a photo (URL or file)"),
    ("create_video_post", "Upload + publish a video (URL or file)"),
    ("create_carousel_post", "Upload + publish a 2-10 photo carousel"),
    ("delete_post", "Delete one of your own posts (confirm required)"),
    ("save_post", "Save/bookmark a post (confirm required)"),
    ("unsave_post", "Remove a post from Saved (confirm required)"),
    # Comments
    ("get_post_comments", "List post comments"),
    ("comment_on_post", "Comment on a post (confirm required)"),
    # Messenger
    ("search_people", "Search people by name"),
    ("get_conversations", "List Messenger conversations"),
    ("get_conversation_messages", "Read conversation messages"),
    ("send_message", "Send a Messenger text message (confirm required)"),
    ("delete_message", "Delete/unsend a Messenger message (confirm required)"),
    ("send_typing_indicator", "Send typing_on/typing_off"),
    ("mark_conversation_seen", "Mark conversation seen"),
    # Groups
    ("get_group", "Get group details"),
    ("get_group_posts", "List group feed posts"),
    ("get_group_members", "List group members"),
    ("search_groups", "Search groups"),
    # Discovery
    ("get_notifications", "List recent notifications"),
    ("get_events", "List upcoming events"),
    # Session
    ("close_session", "Close active scrape clients"),
]


def axi_error(primary: str, detail: str) -> None:
    """AXI §6: Error output (fail loud)."""
    print(f"error: {primary}")
    print(f"help: {detail}")
    sys.exit(1)


def toon_print_dict(data: Any, indent: int = 0) -> None:
    """TOON (Tree-Oriented Object Notation) output for dicts."""
    indent_str = "  " * indent
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, dict):
                print(f"{indent_str}{key}:")
                toon_print_dict(value, indent + 1)
            elif isinstance(value, list):
                print(f"{indent_str}{key}:")
                for item in value:
                    if isinstance(item, dict):
                        toon_print_dict(item, indent + 1)
                    else:
                        print(f"{indent_str}  - {item}")
            else:
                print(f"{indent_str}{key}: {value}")
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                toon_print_dict(item, indent)
            else:
                print(f"{indent_str}- {item}")
    else:
        print(f"{indent_str}{data}")


def toon_print_array(data: list, schema: str, count: int | None = None) -> None:
    """Print array in TOON format with schema."""
    if not data:
        print(f"{schema}: 0")
        return

    if count is not None:
        print(f"count: {len(data)} of {count} total")

    print(f"{schema}[{len(data)}]:")
    for item in data:
        if isinstance(item, dict):
            values = [str(item.get(key, "")) for key in item]
            print(f"  {','.join(values)}")
        else:
            print(f"  {item}")


def _coerce_kwarg(prop_type: str, val: str) -> Any:
    """Coerce a raw CLI string into the parameter's declared JSON type.

    Args:
        prop_type: JSON schema type (string/number/integer/boolean/array/object)
        val: Raw string value from the CLI

    Returns:
        The coerced value

    Raises:
        ValueError: If the value cannot be coerced to the declared type
    """
    if prop_type == "integer":
        return int(val)
    if prop_type == "number":
        return float(val)
    if prop_type == "boolean":
        return val.lower() in ("true", "1", "yes")
    if prop_type in ("array", "object"):
        return json.loads(val)
    return val


def run_tool_direct(tool_name: str, args: list[str], use_json: bool = False) -> None:
    """Execute a tool directly from CLI without the MCP protocol."""

    # Set env var to prevent argparse from processing tool args
    os.environ["FACEBOOK_MCP_TOOL_MODE"] = "1"

    # Temporarily override sys.argv to prevent argparse from processing tool args
    original_argv = sys.argv
    sys.argv = [sys.argv[0]]

    try:
        # Lazy import to avoid argparse conflicts during early interception
        from facebook_mcp_server.server import create_mcp_server
    finally:
        sys.argv = original_argv

    # Parse --key value pairs into a dict
    kwargs: dict[str, Any] = {}
    positional: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg.startswith("--"):
            key = arg[2:]
            if i + 1 < len(args) and not args[i + 1].startswith("--"):
                kwargs[key] = args[i + 1]
                i += 2
            else:
                kwargs[key] = "true"
                i += 1
        else:
            positional.append(arg)
            i += 1

    # Get tool object
    mcp = create_mcp_server()
    tools = asyncio.run(mcp.list_tools())
    tool = next((t for t in tools if t.name == tool_name), None)
    if not tool:
        valid = sorted(t.name for t in tools)
        axi_error(f"Unknown tool: '{tool_name}'", f"Valid tools: {', '.join(valid)}")

    # Map positional args to required params
    schema = tool.parameters or {}
    props = schema.get("properties", {})
    required = schema.get("required", [])
    required_params = [p for p in required if p in props]

    if not required_params and positional:
        axi_error(
            f"Unexpected positional arg: '{positional[0]}'",
            f"Tool `{tool_name}` takes no positional arguments",
        )

    for idx, val in enumerate(positional):
        if idx < len(required_params):
            kwargs[required_params[idx]] = val
        else:
            axi_error(
                f"Unexpected positional arg: '{val}'",
                f"Tool `{tool_name}` expects: {', '.join(required_params)}",
            )

    # Type coercion from schema
    for key, val in list(kwargs.items()):
        if key in props:
            prop = props[key]
            prop_type = prop.get("type", "string")
            try:
                kwargs[key] = _coerce_kwarg(prop_type, val)
            except (ValueError, TypeError, json.JSONDecodeError):
                axi_error(
                    f"Invalid value for `{key}`: '{val}' (expected {prop_type})",
                    f"Tool `{tool_name}` parameter `{key}` expects type {prop_type}",
                )

    # Prepare context for direct CLI execution
    ctx = MockContext()
    kwargs["ctx"] = ctx

    # Call the tool
    try:
        result = asyncio.run(tool.fn(**kwargs))
    except SystemExit:
        raise
    except TypeError as e:
        error_msg = str(e)
        if "missing" in error_msg and "required" in error_msg:
            axi_error(
                f"Tool `{tool_name}` requires additional parameters",
                f"Run `facebook-lyr --tool-info {tool_name}` to see parameters.",
            )
        else:
            axi_error(f"Tool `{tool_name}` failed: {e}", "Check your configuration and try again")
    except Exception as e:
        axi_error(f"Tool `{tool_name}` failed: {e}", "Check your configuration and try again")

    # Output
    if use_json:
        print(json.dumps(result, indent=2))
    else:
        # AXI §1: TOON is default
        toon_print_dict(result)
