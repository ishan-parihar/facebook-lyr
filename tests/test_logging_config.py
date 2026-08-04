"""Tests for logging_config.py — formatters and configuration."""

import json
import logging

from facebook_mcp_server.logging_config import (
    CompactFormatter,
    MCPJSONFormatter,
    configure_logging,
)


class TestFormatters:
    def test_json_formatter(self):
        formatter = MCPJSONFormatter()
        record = logging.LogRecord(
            name="facebook_mcp_server.scraping.api_client",
            level=logging.INFO,
            pathname=__file__,
            lineno=10,
            msg="hello %s",
            args=("world",),
            exc_info=None,
        )
        parsed = json.loads(formatter.format(record))
        assert parsed["level"] == "INFO"
        assert parsed["message"] == "hello world"
        assert parsed["logger"] == "facebook_mcp_server.scraping.api_client"

    def test_compact_formatter_shortens_logger(self):
        formatter = CompactFormatter()
        record = logging.LogRecord(
            name="facebook_mcp_server.scraping.api_client",
            level=logging.INFO,
            pathname=__file__,
            lineno=10,
            msg="hi",
            args=(),
            exc_info=None,
        )
        text = formatter.format(record)
        assert "api_client" in text
        assert "facebook_mcp_server." not in text.split("-")[1]


class TestConfigureLogging:
    def test_configure_json(self):
        configure_logging(log_level="INFO", json_format=True)
        root = logging.getLogger()
        assert root.level == logging.INFO

    def test_configure_compact(self):
        configure_logging(log_level="WARNING", json_format=False)
        root = logging.getLogger()
        assert root.level == logging.WARNING

    def test_configure_invalid_level_falls_back(self):
        configure_logging(log_level="BOGUS")
        root = logging.getLogger()
        assert root.level == logging.WARNING
