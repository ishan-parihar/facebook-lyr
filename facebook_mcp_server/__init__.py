# facebook_mcp_server/__init__.py
"""
Facebook / Messenger MCP Server package.

A Model Context Protocol (MCP) server that gives AI assistants cookie-based
access to Facebook Pages, profiles, Messenger, Groups, and posts: discovery,
reading, comments, and messaging. (Threads.net lives in the sibling
``threads-lyr`` project — anonymous read-only scraping.)

Architecture (mirrors instagram-lyr):
- ``scraping/``     — curl_cffi Chrome-impersonation client + resilient
                      embedded-JSON parsers for the private-web surface.
- ``tools/``        — FastMCP tool registration, one module per domain.
- ``config/``       — layered configuration (CLI args > env > defaults).
- ``session_state`` — secure token/page config + runtime metadata on disk.
- ``bootstrap``     — auth readiness gating for tool calls.
- ``cli_main``      — AXI-compliant CLI (home view, --login, --status, ...).
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("facebook-lyr")
except PackageNotFoundError:
    __version__ = "0.0.0.dev"  # Running from source without install
