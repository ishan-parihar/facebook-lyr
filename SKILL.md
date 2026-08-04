# Facebook / Messenger MCP Server

description: Facebook Pages, Messenger, Groups, and multi-account automation via browser-cookie scraping — profile feeds, posting (text/photo/video/carousel), messaging, comments, and account switching. Use for any task involving a Facebook profile/Page, Messenger conversations, Facebook Groups, or managing multiple Facebook identities.
triggers:
  - "facebook post"
  - "facebook page"
  - "facebook profile"
  - "facebook messenger"
  - "facebook comment"
  - "facebook group"
  - "facebook account switch"
  - "facebook pages admin"
  - "messenger message"
  - "social media posting"
  - "content creation"

> Threads is separate: use the `threads-lyr` project for public Threads reading.

## Overview

The Facebook MCP Server gives agents cookie-authenticated access to the same
facebook.com session the user's browser has:

- **Multi-account:** store multiple identities (user + Pages + profiles), each
  with its own cookie set; switch the active identity; discover which Pages
  the session can act as; import identities from browsers
- **Profiles & Pages:** profile info (bio, follower counts), timeline posts, page/post/person/group search
- **Posts:** create text/photo/video/carousel posts, delete own posts, save/unsave, read post details, comment on posts
- **Messenger:** conversations, message reading/sending/deleting, typing indicators, seen markers
- **Groups:** group info, group feeds, group members, group search
- **Discovery:** notifications and upcoming events for the active identity

## Quick Start

```bash
facebook-lyr                 # Home view with live session state
facebook-lyr --status        # Check cookie session validity
facebook-lyr --login         # Import browser cookies interactively
facebook-lyr --list-tools    # List available tools
facebook-lyr --tool-info <tool>  # Tool parameters
```

## Authentication

- **Interactive:** `facebook-lyr --login` — imports cookies from the user's
  browser (Brave, Chrome, Edge, Firefox, Zen, ...). The session is stored at
  `~/.facebook-lyr/profile/cookies.json` (0600).
- **Headless agents:** export `FACEBOOK_COOKIES` as a JSON object containing
  at least `c_user` and `xs`:
  ```bash
  export FACEBOOK_COOKIES='{"c_user":"12345","xs":"abc..."}'
  ```
- On session expiry, tools return a clear `ToolError` and trigger re-login.

## Multi-Account

```bash
facebook-lyr list_accounts
facebook-lyr list_switchable_accounts      # Pages/profiles this session can act as
facebook-lyr add_account --account_id <id> --name "My Page" --cookies '{"c_user":"..","xs":".."}'
facebook-lyr import_account_from_browser --account_id <id> --name "My Page" --browser zen
facebook-lyr switch_active_account --account_id <id>
facebook-lyr get_active_account_info
facebook-lyr get_account_posting_status --account_id <id>
```

Every read/write tool acts as the **active identity**; pass `--account_id` to
override per call. Posting limits and history are tracked per identity.

## Direct CLI Tool Execution

```bash
facebook-lyr get_me
facebook-lyr get_account_status
facebook-lyr get_page_info --username cocacola
facebook-lyr get_page_posts --username cocacola --max_posts 10
facebook-lyr get_conversations
facebook-lyr send_message --thread_id <tid> --message "Hi" --confirm true
facebook-lyr create_post --status "Hello world" --confirm true
facebook-lyr create_photo_post --source /path/img.jpg --message "caption" --confirm true
facebook-lyr create_video_post --source /path/clip.mp4 --confirm true
facebook-lyr create_carousel_post --sources '["/a.jpg","/b.jpg"]' --confirm true
facebook-lyr delete_post --post_id 123 --confirm true
facebook-lyr save_post --post_id 123 --confirm true
facebook-lyr delete_message --thread_id <tid> --message_id <mid> --confirm true
facebook-lyr get_notifications --limit 10
facebook-lyr get_events --limit 10
facebook-lyr get_group_members --group_id <gid>
facebook-lyr get_page_posts --username cocacola --max_posts 5 --json
```

## MCP Tools

- `get_me` / `get_account_status` / `set_active_page` - account
- `list_accounts` / `list_switchable_accounts` / `add_account` / `remove_account` / `switch_active_account` / `get_active_account_info` / `update_account_cookies` / `import_account_from_browser` / `get_account_posting_status` - multi-account
- `get_page_info` / `get_page_posts` / `search_pages` - Pages
- `get_user_posts` / `get_post_details` / `search_posts` - posts (read)
- `create_post` / `create_photo_post` / `create_video_post` / `create_carousel_post` / `delete_post` / `save_post` / `unsave_post` - post writes (confirm required)
- `get_post_comments` / `comment_on_post` - comments
- `search_people` / `get_conversations` / `get_conversation_messages` / `send_message` / `delete_message` / `send_typing_indicator` / `mark_conversation_seen` - Messenger
- `get_group` / `get_group_posts` / `get_group_members` / `search_groups` - Groups
- `get_notifications` / `get_events` - discovery
- `close_session` - close active scrape clients

## Workflows

1. **Discover:** `get_me` → `list_switchable_accounts` → `get_page_posts` / `get_conversations`
2. **Switch identity:** `switch_active_account --account_id <id>` then act
3. **Publish:** `create_post --status "..." --confirm true` (or photo/video/carousel)
4. **Engage:** `get_post_comments --post_id <id>` → `comment_on_post --post_id <id> --text "..." --confirm true`
5. **Messenger:** `get_conversations` → `get_conversation_messages --thread_id <id>` → `send_message --thread_id <id> --message "..." --confirm true`
6. **Ambient awareness:** `get_notifications` → decide to reply (`get_conversation_messages` → `send_message`), engage (`comment_on_post`), or clean up (`delete_message` / `delete_post` / `save_post`)
7. **Events & groups:** `get_events` → monitor; `get_group_members --group_id <id>` → community pulse

## Safety

Write tools (`create_post`, `create_photo_post`, `create_video_post`,
`create_carousel_post`, `delete_post`, `save_post`, `unsave_post`,
`comment_on_post`, `send_message`, `delete_message`) require
`confirm=true`, validate inputs, respect per-identity daily posting limits,
and are paced with a human-like minimum gap. Respect the user's account and
Facebook's community standards; do not spam posts, comments, or messages.

## Errors

Errors come back as `ToolError` with a next-step hint:

- **Session expired / login wall** → run `--login` or refresh `FACEBOOK_COOKIES`
- **Rate limited (429)** → wait the suggested time and reduce burstiness
- **Page not found (404)** → check the username/URL
- **Write decommissioned ("official API required")** → the account has migrated
  to Facebook's private GraphQL composer; the write cannot be done with cookies.
  The attempt was validated and recorded honestly in the identity's posting
  history — use the official Graph API for that write.
- **Account not found** → `add_account` / `import_account_from_browser` first

## Limitations

- Write endpoints (`updatestatus.php`, `ufi_composer.php`, `send_messages.php`)
  are decommissioned (404) on accounts migrated to the modern GraphQL
  composer — those tools fail honestly instead of faking success.
- Working cookie-era writes: `delete_post` (delete.php), `save_post` /
  `unsave_post` (best-effort), `delete_message` (Mercury delete_messages).
- `get_notifications`, `get_events`, and `get_group_members` parse what the
  initial page embeds; client-side-only data (event detail, member lists on
  private groups) returns an honest empty result.
- Identity switching server-side is decommissioned; act as another identity by
  importing its cookie set (multi-account tools).
- Messenger message bodies on E2EE accounts and group feeds render
  client-side and cannot be scraped from HTML.
- Timeline depth is limited to the first page of embedded JSON.
- Page insights are not available via cookie scraping.
