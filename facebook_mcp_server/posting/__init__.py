"""
Facebook media posting module.

Provides functionality for posting text, photos, videos, and carousels to
Facebook via the cookie-based web surface, with multi-account support —
mirroring instagram-lyr's ``posting`` package.

The write transport (legacy composer endpoints) is best-effort: on accounts
where Facebook has migrated writes to private GraphQL, transport calls fail
cleanly and honestly with a "decommissioned — official API required" error.
Validation, media processing, posting limits, and attempt recording all run
before the transport attempt so every failure mode is explicit.
"""

from facebook_mcp_server.posting.client import PostingClient
from facebook_mcp_server.posting.media_processor import MediaProcessor
from facebook_mcp_server.posting.validators import PostingValidator

__all__ = [
    "PostingClient",
    "PostingValidator",
    "MediaProcessor",
]
