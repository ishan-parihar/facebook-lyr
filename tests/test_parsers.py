"""Unit tests for facebook_mcp_server.scraping.parsers."""

import json

from facebook_mcp_server.scraping import parsers

HOME_HTML = (
    '<script type="application/json" data-sjs="true">{"require":[[{"x":1}]]}</script>'
    "<div>hi</div>"
    '<script type="application/json">{"dtsg":{"token":"TOKEN_123"}}</script>'
)

PROFILE_HTML = (
    '<script type="application/json">'
    + json.dumps(
        {
            "props": {
                "pageProps": {
                    "user": {
                        "userID": "1000001",
                        "name": "Test Person",
                        "shortName": "test.person",
                        "bioText": {"text": "Hello bio"},
                        "profileURL": "https://www.facebook.com/test.person",
                    }
                },
                "counts": [
                    {"count": 42, "shortLabel": "Followers"},
                    {"count": 7, "shortLabel": "Following"},
                ],
            }
        }
    )
    + "</script>"
)

POSTS_HTML = (
    '<script type="application/json">'
    + json.dumps(
        {
            "feed": {
                "units": [
                    {
                        "comet_sections": {"context_layout": {}},
                        "id": "111",
                        "message": {"text": "First post"},
                        "creation_time": 1700000000,
                    },
                    {
                        "comet_sections": {"context_layout": {}},
                        "id": "222",
                        "message": {"text": "Second post"},
                        "creation_time": 1700000001,
                    },
                    {
                        "comet_sections": {"context_layout": {}},
                        "id": "333",
                        "message": {"text": "Third post"},
                        "creation_time": 1700000002,
                    },
                ]
            }
        }
    )
    + "</script>"
)

THREADLIST_JSON = {
    "threads": [
        {
            "thread_fbid": "t_1001",
            "name": "Alice",
            "snippet": "last msg",
            "participants": {
                "1": {"name": "Me", "is_self": True},
                "2": {"name": "Alice", "is_self": False},
            },
            "timestamp": 1700000000,
            "unread_count": 2,
        }
    ]
}

THREAD_JSON = {
    "thread": {
        "messages": [
            {
                "message_id": "m_1",
                "message_sender": {"id": "2", "name": "Alice"},
                "message": {"text": "Hello there"},
                "timestamp_precise": 1700000000,
            }
        ]
    }
}

COMMENTS_JSON = {
    "payload": {
        "comments": [
            {
                "id": "c_1",
                "author": {"id": "2", "name": "Alice"},
                "body": {"text": "Nice post"},
                "timestamp": 1700000000,
            },
            {
                "id": "c_2",
                "author": {"id": "3", "name": "Bob"},
                "body": {"text": "Agreed"},
                "timestamp": 1700000001,
            },
        ]
    }
}


class TestFbDtsg:
    def test_extracts_token(self):
        assert parsers.extract_fb_dtsg(HOME_HTML) == "TOKEN_123"

    def test_none_for_empty(self):
        assert parsers.extract_fb_dtsg("") is None

    def test_none_without_token(self):
        assert parsers.extract_fb_dtsg("<html><body>no token</body></html>") is None

    def test_input_hidden_variant(self):
        html = '<input type="hidden" name="fb_dtsg" value="INPUT_TOKEN">'
        assert parsers.extract_fb_dtsg(html) == "INPUT_TOKEN"


class TestLoginWall:
    def test_login_url_is_wall(self):
        assert parsers.detect_login_wall("<html></html>", "https://www.facebook.com/login/")

    def test_checkpoint_url_is_wall(self):
        assert parsers.detect_login_wall("<html></html>", "https://www.facebook.com/checkpoint/")

    def test_log_in_to_continue_marker(self):
        assert parsers.detect_login_wall('<div>"Log in to continue"</div>', "")

    def test_login_form_without_logout_is_wall(self):
        html = '<form id="login_form"><input name="email"><input name="pass"></form>'
        assert parsers.detect_login_wall(html, "")

    def test_normal_page_not_wall(self):
        html = '<html>Welcome back <a href="/logout">Log Out</a></html>'
        assert not parsers.detect_login_wall(html, "https://www.facebook.com/")

    def test_empty_html_is_wall(self):
        assert parsers.detect_login_wall("", "")


class TestEmbeddedJson:
    def test_parses_all_blocks(self):
        blobs = parsers.parse_embedded_json(HOME_HTML)
        assert len(blobs) == 2
        assert blobs[1]["dtsg"]["token"] == "TOKEN_123"

    def test_skips_malformed(self):
        html = '<script type="application/json">{not json}</script>'
        assert parsers.parse_embedded_json(html) == []

    def test_empty_html(self):
        assert parsers.parse_embedded_json("") == []


class TestDeepFind:
    def test_finds_nested(self):
        data = {"a": [{"b": {"target": 1}}, {"target": 2}]}
        found = parsers.deep_find(data, lambda n: n == 1 or n == 2)
        assert set(found) == {1, 2}

    def test_find_first(self):
        data = {"a": [{"b": {"target": 1}}, {"target": 2}]}
        assert parsers.deep_find_first(data, lambda n: n == 2) == 2

    def test_handles_cycles(self):
        data = {}
        data["self"] = data
        assert parsers.deep_find(data, lambda n: False) == []


class TestProfile:
    def test_parses_profile(self):
        profile = parsers.parse_profile(PROFILE_HTML, "test.person")
        assert profile["id"] == "1000001"
        assert profile["name"] == "Test Person"
        assert profile["bio"] == "Hello bio"
        assert profile["url"] == "https://www.facebook.com/test.person"

    def test_counts(self):
        profile = parsers.parse_profile(PROFILE_HTML, "test.person")
        assert profile.get("follower_count") == 42
        assert profile.get("following_count") == 7


class TestPosts:
    def test_parses_posts_dedupes(self):
        posts = parsers.parse_posts(POSTS_HTML, "test.person", limit=10)
        assert len(posts) == 3
        assert posts[0]["id"] == "111"
        assert posts[0]["text"] == "First post"
        assert posts[0]["url"].startswith("https://www.facebook.com/")

    def test_respects_limit(self):
        posts = parsers.parse_posts(POSTS_HTML, "test.person", limit=2)
        assert len(posts) == 2

    def test_empty_html(self):
        assert parsers.parse_posts("<html></html>", "x") == []


class TestThreads:
    def test_parse_thread_list(self):
        threads = parsers.parse_thread_list(THREADLIST_JSON)
        assert len(threads) == 1
        t = threads[0]
        assert t["thread_id"] == "t_1001"
        assert t["name"] == "Alice"
        assert t["unread_count"] == 2
        assert t["participant_count"] == 2

    def test_parse_thread_list_empty(self):
        assert parsers.parse_thread_list({}) == []
        assert parsers.parse_thread_list({"data": {"threads": []}}) == []

    def test_parse_thread_messages(self):
        messages = parsers.parse_thread_messages(THREAD_JSON)
        assert len(messages) == 1
        assert messages[0]["sender"] == "Alice"
        assert messages[0]["body"] == "Hello there"

    def test_parse_send_result_success(self):
        result = parsers.parse_send_result({"success": True, "message_id": "m_9"})
        assert result["success"] is True
        assert result["message_id"] == "m_9"

    def test_parse_send_result_error(self):
        result = parsers.parse_send_result({"error": {"error_msg": "blocked"}})
        assert result["success"] is False
        assert "blocked" in result["message"]


class TestComments:
    def test_parse_comments(self):
        comments = parsers.parse_comments(COMMENTS_JSON, limit=5)
        assert len(comments) == 2
        assert comments[0]["author"] == "Alice"
        assert comments[0]["text"] == "Nice post"

    def test_parse_comments_limit(self):
        comments = parsers.parse_comments(COMMENTS_JSON, limit=1)
        assert len(comments) == 1

    def test_parse_comments_empty(self):
        assert parsers.parse_comments({}, limit=5) == []
