"""Core exceptions for the Facebook MCP server (scraping layer)."""

from .exceptions import (
    AuthenticationError,
    ElementNotFoundError,
    FacebookScraperException,
    LoginWallError,
    NetworkError,
    PageNotFoundError,
    RateLimitError,
    ScrapingError,
)

__all__ = [
    "AuthenticationError",
    "ElementNotFoundError",
    "FacebookScraperException",
    "LoginWallError",
    "NetworkError",
    "PageNotFoundError",
    "RateLimitError",
    "ScrapingError",
]
