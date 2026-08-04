"""Tests for facebook_mcp_server.session_state."""

import json

from facebook_mcp_server import session_state


def _profile(tmp_path):
    return tmp_path / "facebook-lyr" / "profile"


class TestPaths:
    def test_portable_cookie_path(self, tmp_path):
        profile = _profile(tmp_path)
        assert session_state.portable_cookie_path(profile).name == "cookies.json"
        assert session_state.portable_cookie_path(profile).parent == profile.parent

    def test_source_state_path(self, tmp_path):
        profile = _profile(tmp_path)
        assert session_state.source_state_path(profile).name == "source-state.json"

    def test_profile_exists(self, tmp_path):
        profile = _profile(tmp_path)
        assert session_state.profile_exists(profile) is False
        profile.mkdir(parents=True)
        assert session_state.profile_exists(profile) is False  # empty
        (profile / "file.txt").write_text("x")
        assert session_state.profile_exists(profile) is True


class TestSourceState:
    def test_write_and_load(self, tmp_path):
        profile = _profile(tmp_path)
        state = session_state.write_source_state(profile, preferred_browser="brave")
        assert state.preferred_browser == "brave"
        assert state.login_generation

        loaded = session_state.load_source_state(profile)
        assert loaded is not None
        assert loaded.login_generation == state.login_generation
        assert loaded.preferred_browser == "brave"
        assert loaded.cookies_path == str(session_state.portable_cookie_path(profile))

    def test_load_missing(self, tmp_path):
        assert session_state.load_source_state(_profile(tmp_path)) is None

    def test_load_corrupt(self, tmp_path):
        profile = _profile(tmp_path)
        path = session_state.source_state_path(profile)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{bad json")
        assert session_state.load_source_state(profile) is None


class TestActivePage:
    def test_save_and_load(self, tmp_path):
        profile = _profile(tmp_path)
        saved = session_state.save_active_page("999", "My Page", profile)
        assert saved["page_id"] == "999"
        loaded = session_state.load_active_page(profile)
        assert loaded["page_id"] == "999"
        assert loaded["page_name"] == "My Page"

    def test_load_empty(self, tmp_path):
        assert session_state.load_active_page(_profile(tmp_path)) == {}


class TestClearAuthState:
    def test_clears_all_artifacts(self, tmp_path):
        profile = _profile(tmp_path)
        profile.mkdir(parents=True)
        session_state.write_source_state(profile)
        session_state.save_active_page("1", "p", profile)
        cookie = session_state.portable_cookie_path(profile)
        cookie.write_text(json.dumps({"cookies": []}))

        assert session_state.clear_auth_state(profile) is True
        assert not profile.exists()
        assert not cookie.exists()
        assert not session_state.source_state_path(profile).exists()

    def test_no_artifacts_is_noop(self, tmp_path):
        assert session_state.clear_auth_state(_profile(tmp_path)) is True
