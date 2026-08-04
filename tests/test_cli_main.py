"""Tests for cli_main.py — home view, list tools, login, status, flags."""

import sys

import pytest

from facebook_mcp_server import cli_main
from facebook_mcp_server.session_state import write_source_state


class TestHomeView:
    def test_shows_not_configured_without_session(self, capsys):
        cli_main.show_home_view()
        out = capsys.readouterr().out
        assert "status: not_configured" in out
        assert "tools[" in out
        assert "--login" in out

    def test_shows_valid_with_profile(self, capsys, isolate_profile_dir):
        profile = isolate_profile_dir
        profile.mkdir(parents=True, exist_ok=True)
        (profile / "marker.txt").write_text("x")
        write_source_state(profile, preferred_browser="chrome")
        cli_main.show_home_view()
        out = capsys.readouterr().out
        assert "status: valid" in out
        assert "runtime_id:" in out

    def test_identity_header(self, capsys):
        cli_main.show_home_view()
        out = capsys.readouterr().out
        assert "bin:" in out
        assert "version:" in out
        assert "facebook" in out.lower()


class TestListTools:
    def test_exits_zero_and_lists(self, capsys):
        with pytest.raises(SystemExit) as excinfo:
            cli_main.list_tools_and_exit()
        assert excinfo.value.code == 0
        out = capsys.readouterr().out
        assert "count:" in out
        assert "tools[" in out
        assert "get_page_posts" in out
        assert "get_conversations" in out


class TestStatus:
    def test_no_session_exits_one(self, capsys):
        with pytest.raises(SystemExit) as excinfo:
            cli_main.profile_info_and_exit()
        assert excinfo.value.code == 1
        out = capsys.readouterr().out
        assert "No valid source session" in out

    def test_status_with_session_checks_api(self, capsys, isolate_profile_dir, monkeypatch):
        from tests.conftest import FakeResponse, FakeSession, is_home_url

        profile = isolate_profile_dir
        profile.mkdir(parents=True, exist_ok=True)
        (profile / "marker.txt").write_text("x")  # profile_exists requires non-empty
        write_source_state(profile)
        from facebook_mcp_server.session_state import portable_cookie_path

        cookie_file = portable_cookie_path(profile)
        cookie_file.parent.mkdir(parents=True, exist_ok=True)
        cookie_file.write_text(
            '{"cookies": [{"name": "c_user", "value": "1"}, {"name": "xs", "value": "x"}]}'
        )

        def handler(method, url, params, data, files):
            return FakeResponse(
                status_code=200,
                text='<script type="application/json">{"dtsg":{"token":"T"}}</script>',
                url="https://www.facebook.com/",
            )

        FakeSession.routes = [("GET", is_home_url, handler)]
        monkeypatch.setattr(cli_main, "_AsyncSession", FakeSession)

        with pytest.raises(SystemExit) as excinfo:
            cli_main.profile_info_and_exit()
        assert excinfo.value.code == 0
        out = capsys.readouterr().out
        assert "status: valid" in out


class TestLogin:
    def test_login_without_browsers_exits_nonzero(self, capsys, isolate_profile_dir, monkeypatch):
        monkeypatch.setattr(cli_main, "run_profile_creation", lambda *a, **k: False)
        with pytest.raises(SystemExit) as excinfo:
            cli_main.get_profile_and_exit()
        assert excinfo.value.code == 1


class TestMainDispatch:
    def test_list_tools_flag(self, capsys, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["facebook-lyr", "--list-tools"])
        with pytest.raises(SystemExit) as excinfo:
            cli_main.main()
        assert excinfo.value.code == 0
        out = capsys.readouterr().out
        assert "get_page_posts" in out

    def test_login_flag(self, capsys, monkeypatch):
        monkeypatch.setattr(cli_main, "run_profile_creation", lambda *a, **k: False)
        monkeypatch.setattr(sys, "argv", ["facebook-lyr", "--login"])
        with pytest.raises(SystemExit) as excinfo:
            cli_main.main()
        assert excinfo.value.code == 1

    def test_unknown_tool_flag_fails(self, capsys, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["facebook-lyr", "--tool-info", "nope"])
        with pytest.raises(SystemExit) as excinfo:
            cli_main.main()
        assert excinfo.value.code != 0
        out = capsys.readouterr().out
        assert "error:" in out

    def test_no_args_interactive_shows_home_view(self, capsys, monkeypatch):
        """Interactive terminal + no args → AXI home view (not the server)."""
        monkeypatch.setattr(sys, "argv", ["facebook-lyr"])
        config = cli_main.get_config()
        monkeypatch.setattr(config, "is_interactive", True)
        with pytest.raises(SystemExit) as excinfo:
            cli_main.main()
        assert excinfo.value.code == 0
        out = capsys.readouterr().out
        assert "tools[" in out
        assert "--login" in out

    def test_no_args_non_interactive_starts_server(self, capsys, monkeypatch):
        """MCP client spawns the server with no args on a pipe → must NOT show home view."""
        monkeypatch.setattr(sys, "argv", ["facebook-lyr"])
        config = cli_main.get_config()
        monkeypatch.setattr(config, "is_interactive", False)
        started = {}

        class FakeMCP:
            def __init__(self) -> None:
                started["created"] = True

            def run(self, *args, **kwargs):
                started["ran"] = True
                raise SystemExit(0)

        monkeypatch.setattr(cli_main, "create_mcp_server", lambda: FakeMCP())
        with pytest.raises(SystemExit) as excinfo:
            cli_main.main()
        assert excinfo.value.code == 0
        assert started.get("ran") is True
        out = capsys.readouterr().out
        assert "tools[" not in out  # no home view on the MCP pipe

    def test_hook_flag_forces_home_view_on_pipe(self, capsys, monkeypatch):
        """Session hooks run with piped stdin; --hook must force the home view."""
        monkeypatch.setattr(sys, "argv", ["facebook-lyr", "--hook"])
        config = cli_main.get_config()
        monkeypatch.setattr(config, "is_interactive", False)
        with pytest.raises(SystemExit) as excinfo:
            cli_main.main()
        assert excinfo.value.code == 0
        out = capsys.readouterr().out
        assert "tools[" in out
        assert "session:" in out

    def test_hook_command_uses_flag(self):
        cmd = cli_main._hook_command()
        assert cmd.endswith("--hook")
        # Keeps a path-ish base (absolute, ~-collapsed, or bare command name).
        assert cmd.split()[0]


class TestVersion:
    def test_returns_string(self):
        assert isinstance(cli_main.get_version(), str)
        assert cli_main.get_version()
