"""Custom exceptions for Facebook cookie-scraping operations."""


class FacebookScraperException(Exception):
    """Base exception for the Facebook scraper."""


class AuthenticationError(FacebookScraperException):
    """Raised when the cookie session is invalid, expired, or logged out."""


class LoginWallError(FacebookScraperException):
    """Raised when Facebook serves a login/checkpoint wall instead of content."""


class RateLimitError(FacebookScraperException):
    """Raised when rate limiting / soft-blocking is detected."""

    def __init__(self, message: str, suggested_wait_time: int = 300):
        super().__init__(message)
        self.suggested_wait_time = suggested_wait_time


class NetworkError(FacebookScraperException):
    """Raised when network-level issues occur."""


class ScrapingError(FacebookScraperException):
    """Raised when scraping fails for various reasons (page structure changed)."""


class PageNotFoundError(FacebookScraperException):
    """Raised when a profile/page/group returns 404 or does not exist."""


class ElementNotFoundError(FacebookScraperException):
    """Raised when an expected element is not found in the page data."""
