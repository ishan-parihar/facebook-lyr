"""Project-wide constants for the cookie-scraping Facebook MCP server."""

# Tool timeout — profile fetches and paginated feeds can take a while.
TOOL_TIMEOUT_SECONDS: float = 60.0

# Web property
FACEBOOK_BASE = "https://www.facebook.com"

# Facebook web app id (used by some private-web endpoints).
FB_APP_ID = "134895980765590"

# Default pagination page size when the web surface allows choosing one.
DEFAULT_PAGE_LIMIT = 25

# Max items returned by default from list tools.
DEFAULT_MAX_ITEMS = 50

# Write pacing: minimum gap (s) between user-facing writes (posts, comments,
# Messenger sends) + random jitter, so agent loops don't trip Facebook's
# anti-abuse checks. Read actions are never paced.
_WRITE_MIN_GAP = 8.0
_WRITE_JITTER = 4.0
