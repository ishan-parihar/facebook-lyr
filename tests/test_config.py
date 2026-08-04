"""Tests for the configuration system (cookie mode)."""

import pytest

from facebook_mcp_server.config import get_config, reset_config
from facebook_mcp_server.config.loaders import (
    EnvironmentKeys,
    is_interactive_environment,
    load_config,
    load_from_env,
)
from facebook_mcp_server.config.schema import AppConfig, ConfigurationError


class TestEnvironmentKeys:
    def test_facebook_cookie_keys_present(self):
        assert EnvironmentKeys.FACEBOOK_COOKIES == "FACEBOOK_COOKIES"
        assert EnvironmentKeys.PREFERRED_BROWSER == "FACEBOOK_PREFERRED_BROWSER"
        assert EnvironmentKeys.PROFILE_DIR == "USER_DATA_DIR"


class TestLoadFromEnv:
    def test_profile_dir(self, monkeypatch):
        monkeypatch.setenv("USER_DATA_DIR", "/tmp/custom-profile")
        config = load_from_env(AppConfig())
        assert config.cookie.profile_dir == "/tmp/custom-profile"

    def test_preferred_browser(self, monkeypatch):
        monkeypatch.setenv("FACEBOOK_PREFERRED_BROWSER", "Brave")
        config = load_from_env(AppConfig())
        assert config.cookie.preferred_browser == "brave"

    def test_transport_valid(self, monkeypatch):
        monkeypatch.setenv("TRANSPORT", "streamable-http")
        config = load_from_env(AppConfig())
        assert config.server.transport == "streamable-http"
        assert config.server.transport_explicitly_set is True

    def test_transport_invalid(self, monkeypatch):
        monkeypatch.setenv("TRANSPORT", "bogus")
        with pytest.raises(ConfigurationError):
            load_from_env(AppConfig())

    def test_port_invalid(self, monkeypatch):
        monkeypatch.setenv("PORT", "not-a-port")
        with pytest.raises(ConfigurationError):
            load_from_env(AppConfig())


class TestValidation:
    def test_port_range(self):
        config = AppConfig()
        config.server.port = 0
        with pytest.raises(ConfigurationError):
            config.validate()

    def test_path_format(self):
        config = AppConfig()
        config.server.transport = "streamable-http"
        config.server.path = "mcp"
        with pytest.raises(ConfigurationError):
            config.validate()


class TestLoadConfig:
    def test_defaults(self, monkeypatch):
        monkeypatch.delenv("USER_DATA_DIR", raising=False)
        config = load_config()
        assert config.cookie.profile_dir == "~/.facebook-lyr/profile"
        assert config.server.transport == "stdio"

    def test_get_config_singleton(self, monkeypatch):
        reset_config()
        monkeypatch.setenv("USER_DATA_DIR", "/tmp/singleton-profile")
        config = get_config()
        assert config.cookie.profile_dir == "/tmp/singleton-profile"
        assert get_config() is config
        reset_config()


class TestInteractive:
    def test_returns_bool(self):
        assert isinstance(is_interactive_environment(), bool)
