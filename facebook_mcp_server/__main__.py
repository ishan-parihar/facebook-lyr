"""Entry point for the facebook-mcp-server command."""

import os
import sys


def _maybe_run_direct_tool() -> bool:
    """Intercept direct tool invocation: ``facebook-lyr <tool> [args...]``.

    Returns True if a tool was intercepted and executed (process exits there).
    """
    if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
        # Set env var early before any imports to avoid argparse conflicts.
        os.environ["FACEBOOK_MCP_TOOL_MODE"] = "1"

        from facebook_mcp_server.tool_registry import (
            TOOLS,
            axi_error,
            run_tool_direct,
        )

        tool_name = sys.argv[1]
        tool_names = [t[0] for t in TOOLS]
        if tool_name in tool_names:
            use_json = "--json" in sys.argv
            remaining = [a for a in sys.argv[2:] if a != "--json"]
            run_tool_direct(tool_name, remaining, use_json=use_json)
            sys.exit(0)
        axi_error(
            f"Unknown tool: '{tool_name}'",
            f"Valid tools: {', '.join(tool_names)}",
        )
    return False


def main() -> None:
    """Main entry point that delegates to cli_main (MCP server mode)."""
    from facebook_mcp_server.cli_main import main as cli_main

    cli_main()


if __name__ == "__main__":
    if not _maybe_run_direct_tool():
        main()
