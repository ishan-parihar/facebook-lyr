"""Tool registration modules for the Facebook MCP server."""

from .account_tools import register_account_tools
from .comment_tools import register_comment_tools
from .discovery_tools import register_discovery_tools
from .group_tools import register_group_tools
from .messenger_tools import register_messenger_tools
from .multi_account_tools import register_multi_account_tools
from .page_tools import register_page_tools
from .posting_tools import register_posting_tools
from .post_tools import register_post_tools

__all__ = [
    "register_account_tools",
    "register_comment_tools",
    "register_discovery_tools",
    "register_group_tools",
    "register_messenger_tools",
    "register_multi_account_tools",
    "register_page_tools",
    "register_posting_tools",
    "register_post_tools",
]
