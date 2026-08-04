"""Tests for the bootstrap env-cookie flow."""

import json

from facebook_mcp_server import bootstrap
from facebook_mcp_server.session_state import (
    load_source_state,
    portable_cookie_path,
    profile_exists,
)


class TestEnsureCookiesFromEnv:
    def test_writes_cookie_profile_from_env(self, monkeypatch, isolate_profile_dir):
        monkeypatch.setenv(
            "FACEBOOK_COOKIES",
            json.dumps({"c_user": "42", "xs": "secret", "fr": "f"}),
        )
        bootstrap.reset_bootstrap_for_testing()
        bootstrap._ensure_cookies_from_env()

        profile = isolate_profile_dir
        assert portable_cookie_path(profile).exists()
        assert profile_exists(profile)  # marker file makes it non-empty
        assert load_source_state(profile) is not None

        cookies = json.loads(portable_cookie_path(profile).read_text())
        names = {c["name"] for c in cookies["cookies"]}
        assert {"c_user", "xs", "fr"} <= names

    def test_ignores_missing_xs(self, monkeypatch, isolate_profile_dir, caplog):
        monkeypatch.setenv("FACEBOOK_COOKIES", json.dumps({"c_user": "42"}))
        bootstrap.reset_bootstrap_for_testing()
        bootstrap._ensure_cookies_from_env()
        assert not portable_cookie_path(isolate_profile_dir).exists()

    def test_ignores_invalid_json(self, monkeypatch, isolate_profile_dir, caplog):
        monkeypatch.setenv("FACEBOOK_COOKIES", "{not json")
        bootstrap.reset_bootstrap_for_testing()
        bootstrap._ensure_cookies_from_env()
        assert not portable_cookie_path(isolate_profile_dir).exists()


class TestAuthReady:
    def test_env_session_is_auth_ready(self, monkeypatch, isolate_profile_dir):
        monkeypatch.setenv(
            "FACEBOOK_COOKIES",
            json.dumps({"c_user": "42", "xs": "secret"}),
        )
        bootstrap.reset_bootstrap_for_testing()
        bootstrap.initialize_bootstrap()
        assert bootstrap._auth_ready() is True
        bootstrap.reset_bootstrap_for_testing()
