"""Tests for the facebook multi-account module."""

from __future__ import annotations

import pytest

from facebook_mcp_server import multi_account as ma


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Point the multi-account store at a temp dir."""
    root = tmp_path / "state"
    monkeypatch.setattr(ma, "auth_root_dir", lambda: root)
    return ma


class TestMultiAccountStore:
    def test_create_and_get(self, store):
        acc = store.create_account(
            account_id="1001",
            cookies={"c_user": "1001", "xs": "x"},
            name="User One",
            username="u1",
        )
        assert acc.account_id == "1001"
        got = store.get_account("1001")
        assert got is not None
        assert got.name == "User One"
        assert got.identity_type == "user"

    def test_create_requires_cookies(self, store):
        with pytest.raises(ValueError, match="c_user"):
            store.create_account(account_id="1", cookies={"fr": "z"}, name="Bad")

    def test_list_and_active(self, store):
        store.create_account("1", {"c_user": "1", "xs": "a"}, "A")
        store.create_account("2", {"c_user": "2", "xs": "b"}, "B")
        accounts = store.list_accounts()
        assert {a.account_id for a in accounts} == {"1", "2"}
        assert store.get_active_account().account_id in {"1", "2"}

    def test_set_default_deactivates_others(self, store):
        store.create_account("1", {"c_user": "1", "xs": "a"}, "A")
        store.create_account("2", {"c_user": "2", "xs": "b"}, "B")
        assert store.set_default_account("2")
        active = store.get_active_account()
        assert active.account_id == "2"
        acc1 = store.get_account("1")
        assert acc1.is_active is False

    def test_get_account_cookies_roundtrip(self, store):
        store.create_account("1", {"c_user": "1", "xs": "a", "datr": "d"}, "A")
        cookies = store.get_account_cookies("1")
        assert cookies["c_user"] == "1"
        assert cookies["xs"] == "a"

    def test_set_account_cookies_updates(self, store):
        store.create_account("1", {"c_user": "1", "xs": "a"}, "A")
        assert store.set_account_cookies("1", {"c_user": "1", "xs": "new"})
        assert store.get_account_cookies("1")["xs"] == "new"

    def test_delete_account(self, store):
        store.create_account("1", {"c_user": "1", "xs": "a"}, "A")
        assert store.delete_account("1")
        assert store.get_account("1") is None
        assert not store.delete_account("1")

    def test_posting_limits_and_history(self, store):
        store.create_account("1", {"c_user": "1", "xs": "a"}, "A")
        can_post, _ = store.check_account_posting_limits("1")
        assert can_post

        store.record_post_attempt("1", "text", True, post_id="p1")
        store.record_post_attempt("1", "photo", False, error_message="boom")

        history = store.get_account_posting_history("1")
        assert len(history) == 2
        assert history[0]["success"] is True
        assert history[1]["success"] is False

        acc = store.get_account("1")
        assert acc.post_count_today == 1
        assert acc.last_post_time is not None

    def test_posting_limits_daily_cap(self, store):
        store.create_account(
            "1",
            {"c_user": "1", "xs": "a"},
            "A",
        )
        acc = store.get_account("1")
        acc.daily_post_limit = 2
        store._write_account_metadata(store.account_metadata_path("1"), acc)
        for _ in range(2):
            store.record_post_attempt("1", "text", True)
        can_post, reason = store.check_account_posting_limits("1")
        assert not can_post
        assert "limit" in reason


class TestMultiAccountTools:
    async def test_register_and_call_list(self):
        from facebook_mcp_server.server import create_mcp_server

        mcp = create_mcp_server()
        tools = await mcp.list_tools()
        names = {t.name for t in tools}
        for expected in (
            "list_accounts",
            "switch_active_account",
            "get_active_account_info",
            "add_account",
            "remove_account",
            "update_account_cookies",
            "get_account_posting_status",
            "list_switchable_accounts",
            "import_account_from_browser",
        ):
            assert expected in names

    async def test_add_and_switch_account(self, store, monkeypatch):
        from facebook_mcp_server.server import create_mcp_server

        mcp = create_mcp_server()
        tools = {t.name: t for t in await mcp.list_tools()}

        result = await tools["add_account"].fn(
            account_id="4242",
            name="Page Admin",
            cookies={"c_user": "4242", "xs": "secret"},
            identity_type="page",
            set_as_active=True,
        )
        assert result["success"] is True
        assert store.get_active_account().account_id == "4242"

        switched = await tools["switch_active_account"].fn(account_id="4242")
        assert switched["success"] is True
        assert switched["active_account_id"] == "4242"

        info = await tools["get_active_account_info"].fn()
        assert info["active_account"]["account_id"] == "4242"

    async def test_remove_account(self, store):
        from facebook_mcp_server.server import create_mcp_server

        store.create_account("7", {"c_user": "7", "xs": "s"}, "Seven")
        mcp = create_mcp_server()
        tools = {t.name: t for t in await mcp.list_tools()}
        result = await tools["remove_account"].fn(account_id="7")
        assert result["success"] is True
        assert store.get_account("7") is None

    async def test_get_account_posting_status(self, store):
        from facebook_mcp_server.server import create_mcp_server

        store.create_account("5", {"c_user": "5", "xs": "s"}, "Five")
        store.set_default_account("5")
        mcp = create_mcp_server()
        tools = {t.name: t for t in await mcp.list_tools()}
        result = await tools["get_account_posting_status"].fn(account_id="5")
        assert result["account_id"] == "5"
        assert result["can_post"] is True
