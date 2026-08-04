"""Scraping engine — cookie-based Facebook private-web API client."""

from .api_client import FacebookScrapeClient
from .fields import PROFILE_SECTIONS, parse_profile_sections
from .identity import IMPERSONATE, USER_AGENT

__all__ = [
    "IMPERSONATE",
    "PROFILE_SECTIONS",
    "USER_AGENT",
    "FacebookScrapeClient",
    "parse_profile_sections",
]
