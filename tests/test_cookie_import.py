"""Tests for facebook_mcp_server.cookie_import."""

import json

from facebook_mcp_server.cookie_import import (
    BROWSER_REGISTRY,
    get_saved_browser_preference,
    load_cookies_from_file,
    save_cookies_to_profile,
    validate_cookies,
)


class TestValidateCookies:
    def test_valid_pair(self):
        ok, missing = validate_cookies({"c_user": "1", "xs": "abc"})
        assert ok is True
        assert missing == set()

    def test_missing_xs(self):
        ok, missing = validate_cookies({"c_user": "1"})
        assert ok is False
        assert "xs" in missing

    def test_empty(self):
        ok, missing = validate_cookies({})
        assert ok is False
        assert missing == {"c_user", "xs"}


class TestLoadCookiesFromFile:
    def test_portable_format(self, tmp_path):
        path = tmp_path / "cookies.json"
        path.write_text(
            json.dumps(
                {
                    "cookies": [
                        {"name": "c_user", "value": "1", "domain": ".facebook.com"},
                        {"name": "xs", "value": "abc", "domain": ".facebook.com"},
                        {"name": "irrelevant", "value": "x", "domain": ".example.com"},
                    ],
                    "imported_from": "chrome",
                }
            )
        )
        cookies = load_cookies_from_file(path)
        assert cookies == {"c_user": "1", "xs": "abc"}

    def test_flat_dict(self, tmp_path):
        path = tmp_path / "cookies.json"
        path.write_text(json.dumps({"c_user": "1", "xs": "abc", "fr": "frval"}))
        cookies = load_cookies_from_file(path)
        assert cookies["c_user"] == "1"
        assert "fr" in cookies

    def test_browser_export_list(self, tmp_path):
        path = tmp_path / "cookies.json"
        path.write_text(
            json.dumps(
                [
                    {"name": "c_user", "value": "1", "domain": ".facebook.com"},
                    {"name": "xs", "value": "abc", "domain": ".facebook.com"},
                    {"name": "datr", "value": "datrval", "domain": ".facebook.com"},
                ]
            )
        )
        cookies = load_cookies_from_file(path)
        assert cookies["c_user"] == "1"
        assert cookies["datr"] == "datrval"

    def test_missing_file(self, tmp_path):
        assert load_cookies_from_file(tmp_path / "nope.json") is None

    def test_corrupt_file(self, tmp_path):
        path = tmp_path / "cookies.json"
        path.write_text("{not json")
        assert load_cookies_from_file(path) is None

    def test_ignores_foreign_domains(self, tmp_path):
        path = tmp_path / "cookies.json"
        path.write_text(
            json.dumps(
                {"cookies": [{"name": "sessionid", "value": "x", "domain": ".instagram.com"}]}
            )
        )
        assert load_cookies_from_file(path) is None


class TestSaveCookies:
    def test_saves_with_0600(self, tmp_path):
        ok = save_cookies_to_profile(
            {"c_user": "1", "xs": "abc"}, tmp_path, source_browser="chrome"
        )
        assert ok is True
        cookie_file = tmp_path.parent / "cookies.json"
        assert cookie_file.exists()
        assert cookie_file.stat().st_mode & 0o777 == 0o600
        data = json.loads(cookie_file.read_text())
        assert data["imported_from"] == "chrome"
        assert len(data["cookies"]) == 2

    def test_saved_browser_preference(self, tmp_path):
        save_cookies_to_profile({"c_user": "1", "xs": "a"}, tmp_path, source_browser="zen")
        cookie_file = tmp_path.parent / "cookies.json"
        assert get_saved_browser_preference(cookie_file) == "zen"

    def test_no_preference_when_missing(self, tmp_path):
        assert get_saved_browser_preference(tmp_path / "nope.json") is None


class TestBrowserRegistry:
    def test_contains_major_browsers(self):
        for name in ("chrome", "brave", "firefox", "edge"):
            assert name in BROWSER_REGISTRY

    def test_browser_profiles_have_paths(self):
        for profile in BROWSER_REGISTRY.values():
            assert profile.executable_paths
            assert profile.profile_dir_paths
