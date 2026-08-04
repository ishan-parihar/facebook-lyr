# facebook_mcp_server/logging_config.py
"""
Logging configuration for the Facebook MCP Server with format options.

Provides JSON and compact logging formats for different deployment scenarios.
JSON format for production MCP integration, compact format for development.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_TRACE_FILE_HANDLER: logging.Handler | None = None
_TRACE_CLEANUP_REGISTERED = False


def get_trace_dir() -> Path | None:
    """Return the trace directory from FACEBOOK_TRACE_DIR, if configured."""
    raw = os.environ.get("FACEBOOK_TRACE_DIR", "").strip()
    return Path(raw).expanduser().resolve() if raw else None


class MCPJSONFormatter(logging.Formatter):
    """JSON formatter for MCP server logs."""

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON."""
        log_data: dict[str, Any] = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_data)


class CompactFormatter(logging.Formatter):
    """Compact formatter that shortens logger names and timestamps."""

    def format(self, record: logging.LogRecord) -> str:
        """Format log record with compact formatting."""
        record_copy = logging.LogRecord(
            name=record.name,
            level=record.levelno,
            pathname=record.pathname,
            lineno=record.lineno,
            msg=record.msg,
            args=record.args,
            exc_info=record.exc_info,
            func=record.funcName,
        )
        record_copy.stack_info = record.stack_info

        record_copy.name = record_copy.name.removeprefix("facebook_mcp_server.")

        record_copy.asctime = self.formatTime(record_copy, datefmt="%H:%M:%S")

        return f"{record_copy.asctime} - {record_copy.name} - {record.levelname} - {record.getMessage()}"


def configure_logging(log_level: str = "WARNING", json_format: bool = False) -> None:
    """Configure logging for the Facebook MCP Server.

    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR)
        json_format: Whether to use JSON formatting for logs
    """
    numeric_level = getattr(logging, log_level.upper(), logging.WARNING)

    formatter = MCPJSONFormatter() if json_format else CompactFormatter()

    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)

    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            # Best-effort: handler may already be closed.
            logger.debug("Failed to close log handler", exc_info=True)

    global _TRACE_CLEANUP_REGISTERED, _TRACE_FILE_HANDLER
    _TRACE_FILE_HANDLER = None

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    trace_dir = get_trace_dir()
    if trace_dir is not None:
        trace_dir.mkdir(parents=True, exist_ok=True)
        log_path = trace_dir / "server.log"
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)
        _TRACE_FILE_HANDLER = file_handler
        if not _TRACE_CLEANUP_REGISTERED:
            atexit.register(teardown_trace_logging)
            _TRACE_CLEANUP_REGISTERED = True

    # Reduce external library noise.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.ERROR)


def teardown_trace_logging(*, keep_traces: bool = False) -> None:
    """Close trace logging handlers; optionally leave trace files in place."""
    global _TRACE_FILE_HANDLER

    if _TRACE_FILE_HANDLER is not None:
        root_logger = logging.getLogger()
        root_logger.removeHandler(_TRACE_FILE_HANDLER)
        try:
            _TRACE_FILE_HANDLER.close()
        finally:
            _TRACE_FILE_HANDLER = None

    if not keep_traces:
        # Remove the trace dir only if it is empty of other artifacts.
        trace_dir = get_trace_dir()
        if trace_dir is not None and trace_dir.exists():
            try:
                if not any(trace_dir.iterdir()):
                    trace_dir.rmdir()
            except OSError:
                # Best-effort cleanup at exit.
                pass
