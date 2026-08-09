---
name: facebook-lyr
description: Facebook/Messenger/Threads automation with profiles, posts, Messenger, and analytics. Use this skill whenever the user requests Facebook operations, social media posting, content creation, or any Facebook-related tasks. Triggers on phrases like "facebook post", "facebook search", "facebook message", "facebook automation", "social media posting", "content creation", "messenger", "facebook profile", "search facebook", or any request for Facebook functionality.
---

# Facebook-lyr Skill

This skill enables AI agents to interact with Facebook/Messenger/Threads using the facebook-lyr CLI tool. It provides comprehensive Facebook automation including profiles, posts, Messenger, and analytics.

## Prerequisites

- facebook-lyr CLI must be installed globally on the system
- The CLI must be accessible in the system PATH
- Browser cookies are required for authenticated operations

## Command Structure

### Available Commands

```bash
# Session management
facebook-lyr                          # Start MCP server
facebook-lyr --login                  # Import browser cookies
facebook-lyr --status                 # Check session status
facebook-lyr --list-tools             # List available MCP tools

# Content commands (examples via MCP tools)
# Get person profile
facebook-lyr get_person_profile --facebook_username zuck --sections posts

# Get page profile
facebook-lyr get_page_profile --facebook_username meta --sections posts

# Search users
facebook-lyr search_users --query "software engineer" --limit 10

# Get feed
facebook-lyr get_feed --num_posts 10
```

## MCP Tool Usage

The facebook-lyr CLI serves as an MCP server with the following tools:

### Profile Tools
- `get_user_profile` - Get user profile with optional sections
- `get_page_profile` - Get page profile with optional sections

### Content Tools
- `get_user_posts` - Get user's posts
- `get_user_reels` - Get user's reels
- `get_user_stories` - Get user's stories

### Messenger Tools
- `get_thread_list` - Get list of conversation threads
- `get_thread_info` - Get details of a specific conversation
- `send_message` - Send a message

### Engagement Tools
- `like_post` - Like a post
- `comment_on_post` - Comment on a post

### Session Tools
- `session_status` - Check the current session status

## MCP Configuration

```json
{
  "mcpServers": {
    "facebook": {
      "command": "uv",
      "args": ["run", "-m", "facebook_mcp_server"],
      "cwd": "/path/to/facebook-lyr",
      "env": { "FACEBOOK_COOKIES": "{\"c_user\":\"...\",\"xs\":\"...\"}" }
    }
  }
}
```

Or using uvx (no clone needed):
```json
{
  "mcpServers": {
    "facebook": {
      "command": "uvx",
      "args": ["facebook-lyr"]
    }
  }
}
```

## Authentication

Set `FACEBOOK_COOKIES` env var or run `--login` for browser-based auth:

```bash
facebook-lyr --login
```

## Session Integration

The CLI will automatically check for existing Facebook session at `~/.facebook-lyr/profile/cookies.json`.

## Workflow

### Step 1: Analyze User Request
- Determine the type of Facebook operation needed (profile, search, post, message)
- Identify if authentication is required
- Select appropriate command based on user intent

### Step 2: Generate Command
- Use the command structure above to build the appropriate CLI command
- Add relevant flags for sections, limits, and output format
- Include any required parameters (usernames, query terms, etc.)

### Step 3: Execute and Validate
- Run the command using shell execution
- Check for successful completion
- Parse the output in the appropriate format
- Report the result to the user

### Step 4: Handle Errors
- If authentication fails: guide user to run `facebook-lyr --login`
- If command not found: suggest installing facebook-lyr
- If rate limited: suggest waiting before retrying
- If invalid parameters: provide correct usage examples

## Error Handling

### Common Issues and Solutions

1. **Command not found**
   - Error: "facebook-lyr: command not found"
   - Solution: Install facebook-lyr globally or add to PATH

2. **Authentication errors**
   - Error: "Authentication required"
   - Solution: Run `facebook-lyr --login` to import browser cookies

3. **Rate limiting**
   - Error: "Rate limit exceeded"
   - Solution: Wait before retrying the operation

4. **Invalid parameters**
   - Error: "Invalid username/URL"
   - Solution: Verify the username or URL is correct

## Integration Example

### User Request: "Get Mark Zuckerberg's Facebook profile"

**Skill Processing:**
1. Identify as a profile operation
2. Generate command: `facebook-lyr get_user_profile --facebook_username zuck --sections posts`
3. Execute and parse results
4. Report profile to user

### User Request: "Get Meta's Facebook page posts"

**Skill Processing:**
1. Identify as a page operation
2. Generate command: `facebook-lyr get_page_profile --facebook_username meta --sections posts`
3. Execute and format results
4. Report page posts to user

### User Request: "Send a message via Messenger"

**Skill Processing:**
1. Identify as a write operation requiring authentication
2. Generate command: `facebook-lyr send_message --recipient <user> --text "Message"`
3. Execute and report results

## Best Practices

1. **Always check authentication requirements** before executing write operations
2. **Use appropriate sections** when getting profiles (posts, experience, skills, etc.)
3. **Handle rate limiting gracefully** by suggesting delays between operations
4. **Provide context in results** when returning content data (author, timestamp, metrics)
5. **Use reasonable limits** when searching to avoid overwhelming results
6. **Handle errors with actionable suggestions** for resolution
7. **Verify usernames** before messaging to ensure they exist