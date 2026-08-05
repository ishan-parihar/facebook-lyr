"""
Configuration loading and argument parsing for the Facebook MCP Server.

Loads settings from CLI arguments and environment variables.
"""

import argparse
import logging
import os
import sys
from typing import Literal, cast

from dotenv import load_dotenv

from .schema import AppConfig, ConfigurationError

load_dotenv()

logger = logging.getLogger(__name__)


def _normalize_env(value: str) -> str:
    return value.strip().lower()


def positive_int(value: str) -> int:
    ivalue = int(value)
    if ivalue <= 0:
        raise argparse.ArgumentTypeError(f"must be positive, got {value}")
    return ivalue


class EnvironmentKeys:
    LOG_LEVEL = "LOG_LEVEL"
    TRANSPORT = "TRANSPORT"
    HOST = "HOST"
    PORT = "PORT"
    HTTP_PATH = "HTTP_PATH"
    PROFILE_DIR = "USER_DATA_DIR"
    PREFERRED_BROWSER = "FACEBOOK_PREFERRED_BROWSER"
    FACEBOOK_COOKIES = "FACEBOOK_COOKIES"
    GRAPH_API_TOKEN = "FACEBOOK_GRAPH_API_TOKEN"
    GRAPH_API_PAGE_ID = "FACEBOOK_GRAPH_API_PAGE_ID"


def is_interactive_environment() -> bool:
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, OSError):
        return False


def load_from_env(config: AppConfig) -> AppConfig:
    """Load configuration from environment variables."""
    if log_level_env := os.environ.get(EnvironmentKeys.LOG_LEVEL):
        log_level_upper = log_level_env.strip().upper()
        if log_level_upper in ("DEBUG", "INFO", "WARNING", "ERROR"):
            config.server.log_level = cast(
                Literal["DEBUG", "INFO", "WARNING", "ERROR"], log_level_upper
            )

    if transport_env := os.environ.get(EnvironmentKeys.TRANSPORT):
        config.server.transport_explicitly_set = True
        transport_value = _normalize_env(transport_env)
        if transport_value == "stdio":
            config.server.transport = "stdio"
        elif transport_value == "streamable-http":
            config.server.transport = "streamable-http"
        else:
            raise ConfigurationError(f"Invalid TRANSPORT: '{transport_env}'.")

    if profile_dir_env := os.environ.get(EnvironmentKeys.PROFILE_DIR):
        config.cookie.profile_dir = profile_dir_env

    if host_env := os.environ.get(EnvironmentKeys.HOST):
        config.server.host = host_env

    if port_env := os.environ.get(EnvironmentKeys.PORT):
        try:
            config.server.port = int(port_env)
        except ValueError:
            raise ConfigurationError(f"Invalid PORT: '{port_env}'. Must be an integer.")

    if path_env := os.environ.get(EnvironmentKeys.HTTP_PATH):
        config.server.path = path_env

    if browser_env := os.environ.get(EnvironmentKeys.PREFERRED_BROWSER):
        config.cookie.preferred_browser = browser_env.strip().lower()

    return config


def load_from_args(config: AppConfig) -> AppConfig:
    """Load configuration from command line arguments."""
    # Skip argparse parsing if in tool mode (to avoid conflicts with direct
    # tool invocation from the CLI).
    if os.environ.get("FACEBOOK_MCP_TOOL_MODE") == "1":
        return config

    parser = argparse.ArgumentParser(
        description="Facebook MCP Server - A Model Context Protocol server for "
        "Facebook / Messenger / Threads integration via browser cookies"
    )

    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Set logging level (default: WARNING)",
    )

    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default=None,
        help="Specify the transport mode (stdio or streamable-http)",
    )

    parser.add_argument("--host", type=str, default=None, help="HTTP server host")
    parser.add_argument("--port", type=int, default=None, help="HTTP server port")
    parser.add_argument("--path", type=str, default=None, help="HTTP server path")

    # Session management
    parser.add_argument(
        "--login",
        action="store_true",
        help="Log in interactively by importing cookies from your browser",
    )

    parser.add_argument(
        "--status",
        action="store_true",
        help="Check if current session is valid and exit",
    )

    parser.add_argument(
        "--logout",
        action="store_true",
        help="Clear stored Facebook profile",
    )

    # AXI §8/§9: tool discovery and introspection
    parser.add_argument(
        "--list-tools",
        action="store_true",
        help="List all available MCP tools and exit",
    )

    parser.add_argument(
        "--tool-info",
        type=str,
        metavar="TOOL",
        help="Show details for a specific tool and exit",
    )

    # AXI §7: Session integrations
    parser.add_argument(
        "--install-hook",
        action="store_true",
        help="Install session hooks for Claude Code/Codex (AXI §7)",
    )

    parser.add_argument(
        "--install-skill",
        action="store_true",
        help="Install agent skill for task-based discovery (AXI §7)",
    )

    # AXI §7: session-hook output (forces the home view even with piped stdin)
    parser.add_argument(
        "--hook",
        action="store_true",
        help="Print the session home view (used by SessionStart hooks)",
    )

    parser.add_argument(
        "--user-data-dir",
        type=str,
        default=None,
        metavar="PATH",
        help="Path to profile directory (default: ~/.facebook-lyr/profile)",
    )

    parser.add_argument(
        "--browser",
        type=str,
        default=None,
        metavar="BROWSER",
        help="Browser to import cookies from (brave, chrome, edge, firefox, zen, ...)",
    )

    parser.add_argument(
        "--json",
        action="store_true",
        help="Render tool results as JSON (default is TOON)",
    )

    args = parser.parse_args()

    if args.log_level:
        config.server.log_level = args.log_level

    if args.transport:
        config.server.transport = args.transport
        config.server.transport_explicitly_set = True

    if args.host:
        config.server.host = args.host

    if args.port:
        config.server.port = args.port

    if args.path:
        config.server.path = args.path

    if args.login:
        config.server.login = True

    if args.status:
        config.server.status = True

    if args.logout:
        config.server.logout = True

    if args.list_tools:
        config.server.list_tools = True

    if args.tool_info:
        config.server.tool_info = args.tool_info

    if args.install_hook:
        config.server.install_hook = True

    if args.install_skill:
        config.server.install_skill = True

    if args.hook:
        config.server.hook = True

    if args.user_data_dir:
        config.cookie.profile_dir = args.user_data_dir

    if args.browser:
        config.cookie.preferred_browser = args.browser.lower()

    if args.json:
        config.server.json_output = True

    return config


def load_config() -> AppConfig:
    """
    Load configuration with clear precedence order:
    1. Command line arguments (highest priority)
    2. Environment variables
    3. Defaults (lowest priority)
    """
    config = AppConfig()
    config.is_interactive = is_interactive_environment()
    logger.debug(f"Interactive mode: {config.is_interactive}")

    config = load_from_env(config)
    config = load_from_args(config)
    config.validate()

    return config
