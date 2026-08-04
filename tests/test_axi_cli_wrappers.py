"""Tests for the AXI CLI wrappers — direct tool interception."""

import sys

import pytest

from facebook_mcp_server import cli_wrapper
from facebook_mcp_server.tool_registry import axi_error


class TestCliWrapper:
    def test_unknown_tool_intercepted(self, capsys, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["facebook-lyr", "bogus_tool"])
        with pytest.raises(SystemExit) as excinfo:
            cli_wrapper.main()
        assert excinfo.value.code == 1
        out = capsys.readouterr().out
        assert "error: Unknown tool" in out
        assert "help:" in out

    def test_list_tools_flag(self, capsys, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["facebook-lyr", "--list-tools"])
        with pytest.raises(SystemExit) as excinfo:
            cli_wrapper.main()
        assert excinfo.value.code == 0
        out = capsys.readouterr().out
        assert "get_page_posts" in out


class TestAxiError:
    def test_prints_error_and_exits(self, capsys):
        with pytest.raises(SystemExit) as excinfo:
            axi_error("Something failed", "Try again")
        assert excinfo.value.code == 1
        out = capsys.readouterr().out
        assert out == "error: Something failed\nhelp: Try again\n"


class TestToolRegistry:
    def test_tools_list_contains_core_tools(self):
        from facebook_mcp_server.tool_registry import TOOLS

        names = {name for name, _ in TOOLS}
        assert "create_post" in names
        assert "send_message" in names
        assert "get_group" in names

    def test_tools_are_serializable(self):
        from facebook_mcp_server.tool_registry import TOOLS

        for name, desc in TOOLS:
            assert name
            assert desc

    def test_run_tool_direct_unknown(self, capsys, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["facebook-lyr", "run_tool_direct"])
        monkeypatch.delenv("FACEBOOK_MCP_TOOL_MODE", raising=False)
        from facebook_mcp_server.tool_registry import run_tool_direct

        with pytest.raises(SystemExit):
            run_tool_direct("not_a_real_tool", [])
        out = capsys.readouterr().out
        assert "Unknown tool" in out
