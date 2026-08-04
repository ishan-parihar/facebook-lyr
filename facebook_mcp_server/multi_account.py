"""
Multi-account management for the Facebook MCP Server.

Provides functionality to manage multiple Facebook identities (user accounts
and Pages the session can act as) with separate cookie stores, so agents can
switch which identity they operate as — mirroring instagram-lyr's
``multi_account.py``.

Each stored identity keeps its own cookie jar (at minimum ``c_user`` + ``xs``).
Switching the active account changes which cookie set the scrape client loads,
so every read/write tool acts as the selected identity.
"""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import asdict, dataclass
from datetime import UTC
from pathlib import Path

from facebook_mcp_server.common_utils import secure_write_text, utcnow_iso
from facebook_mcp_server.session_state import auth_root_dir

logger = logging.getLogger(__name__)

_ACCOUNTS_DIR = "accounts"
_ACCOUNT_METADATA_FILE = "account-metadata.json"
_ACCOUNT_COOKIES_FILE = "cookies.json"

_REQUIRED_COOKIES = {"c_user", "xs"}


@dataclass
class AccountMetadata:
    """Metadata for a stored Facebook identity."""

    account_id: str
    name: str
    username: str = ""
    identity_type: str = "user"  # user | page | profile
    profile_pic_url: str | None = None
    created_at: str | None = None
    last_used: str | None = None
    is_active: bool = True

    # Posting-specific fields (mirror instagram-lyr)
    posting_enabled: bool = True
    daily_post_limit: int = 10
    last_post_time: str | None = None
    post_count_today: int = 0
    last_reset_date: str | None = None


def accounts_root_dir() -> Path:
    """Return the root directory for all account profiles."""
    return auth_root_dir() / _ACCOUNTS_DIR


def account_dir(account_id: str) -> Path:
    """Return the directory for a specific account."""
    return accounts_root_dir() / account_id


def account_metadata_path(account_id: str) -> Path:
    """Return the metadata file path for a specific account."""
    return account_dir(account_id) / _ACCOUNT_METADATA_FILE


def account_cookies_path(account_id: str) -> Path:
    """Return the cookies file path for a specific account."""
    return account_dir(account_id) / _ACCOUNT_COOKIES_FILE


def list_accounts() -> list[AccountMetadata]:
    """List all stored Facebook identities (most recently used first)."""
    accounts_dir = accounts_root_dir()
    if not accounts_dir.exists():
        return []

    accounts: list[AccountMetadata] = []
    for account_path in accounts_dir.iterdir():
        if not account_path.is_dir():
            continue
        metadata_path = account_metadata_path(account_path.name)
        if not metadata_path.exists():
            continue
        metadata = _load_account_metadata(metadata_path)
        if metadata:
            accounts.append(metadata)

    accounts.sort(key=lambda x: x.last_used or "", reverse=True)
    return accounts


def get_account(account_id: str) -> AccountMetadata | None:
    """Get metadata for a specific account."""
    metadata_path = account_metadata_path(account_id)
    if not metadata_path.exists():
        return None
    return _load_account_metadata(metadata_path)


def create_account(
    account_id: str,
    cookies: dict[str, str],
    name: str,
    username: str = "",
    identity_type: str = "user",
    profile_pic_url: str | None = None,
) -> AccountMetadata:
    """Create a new account profile with cookies.

    Args:
        account_id: The Facebook account/page id (numeric string from c_user)
        cookies: Cookie dict containing at least c_user and xs
        name: Display name
        username: Vanity username if known
        identity_type: user, page, or profile
        profile_pic_url: Optional profile picture URL

    Returns:
        The created AccountMetadata

    Raises:
        ValueError: If required cookies are missing
    """
    missing = _REQUIRED_COOKIES - set(cookies.keys())
    if missing:
        raise ValueError(f"Missing required cookies: {sorted(missing)}")

    account_path = account_dir(account_id)
    account_path.mkdir(parents=True, exist_ok=True)

    metadata = AccountMetadata(
        account_id=account_id,
        name=name,
        username=username,
        identity_type=identity_type,
        profile_pic_url=profile_pic_url,
        created_at=utcnow_iso(),
        last_used=utcnow_iso(),
        is_active=True,
    )

    _write_account_metadata(account_metadata_path(account_id), metadata)
    _write_account_cookies(account_cookies_path(account_id), cookies)

    logger.info("Created account profile: %s (%s)", name, account_id)
    return metadata


def update_account_last_used(account_id: str) -> None:
    """Update the last_used timestamp for an account."""
    metadata = get_account(account_id)
    if not metadata:
        return
    metadata.last_used = utcnow_iso()
    _write_account_metadata(account_metadata_path(account_id), metadata)


def delete_account(account_id: str) -> bool:
    """Delete an account profile."""
    account_path = account_dir(account_id)
    if not account_path.exists():
        logger.warning("Account not found: %s", account_id)
        return False
    try:
        shutil.rmtree(account_path)
        logger.info("Deleted account: %s", account_id)
        return True
    except OSError as e:
        logger.error("Failed to delete account %s: %s", account_id, e)
        return False


def get_account_cookies(account_id: str) -> dict[str, str] | None:
    """Get cookies for a specific account."""
    cookies_path = account_cookies_path(account_id)
    if not cookies_path.exists():
        return None
    return _load_account_cookies(cookies_path)


def set_account_cookies(account_id: str, cookies: dict[str, str]) -> bool:
    """Update cookies for a specific account."""
    cookies_path = account_cookies_path(account_id)
    if not cookies_path.exists():
        logger.warning("Account not found: %s", account_id)
        return False
    _write_account_cookies(cookies_path, cookies)
    update_account_last_used(account_id)
    return True


def get_active_account() -> AccountMetadata | None:
    """Get the most recently used active account."""
    for account in list_accounts():
        if account.is_active:
            return account
    return None


def set_default_account(account_id: str) -> bool:
    """Set an account as the default (active) account."""
    metadata = get_account(account_id)
    if not metadata:
        return False

    # Deactivate all accounts
    for account in list_accounts():
        if account.is_active:
            account.is_active = False
            _write_account_metadata(account_metadata_path(account.account_id), account)

    # Activate the specified account
    metadata.is_active = True
    metadata.last_used = utcnow_iso()
    _write_account_metadata(account_metadata_path(account_id), metadata)

    logger.info("Set default account: %s (%s)", metadata.name, account_id)
    return True


# ── posting limits (mirror instagram-lyr) ──────────────────────────────────


def check_account_posting_limits(account_id: str) -> tuple[bool, str]:
    """Check if an account has reached posting limits."""
    account = get_account(account_id)
    if not account:
        return False, "Account not found"

    if not account.posting_enabled:
        return False, "Posting disabled for this account"

    if account.daily_post_limit > 0 and account.post_count_today >= account.daily_post_limit:
        return (
            False,
            f"Daily posting limit reached ({account.post_count_today}/{account.daily_post_limit})",
        )

    if account.last_post_time:
        from datetime import datetime, timedelta

        last_post = datetime.fromisoformat(account.last_post_time)
        # Normalize naive timestamps (older records may lack tz) before
        # comparing against an aware UTC clock.
        if last_post.tzinfo is None:
            last_post = last_post.replace(tzinfo=UTC)
        cooldown = timedelta(minutes=30)  # 30-minute cooldown
        if datetime.now(UTC) < last_post + cooldown:
            remaining = (last_post + cooldown) - datetime.now(UTC)
            return (
                False,
                f"Account in cooldown ({remaining.seconds // 60} minutes remaining)",
            )

    return True, ""


def increment_account_post_count(account_id: str) -> None:
    """Increment the daily post count for an account (with day reset)."""
    account = get_account(account_id)
    if not account:
        return

    from datetime import UTC, datetime

    if account.last_reset_date:
        last_reset = datetime.fromisoformat(account.last_reset_date)
        if last_reset.date() < datetime.now(UTC).date():
            account.post_count_today = 0
            account.last_reset_date = datetime.now(UTC).isoformat()

    account.post_count_today += 1
    account.last_post_time = datetime.now(UTC).isoformat()
    _write_account_metadata(account_metadata_path(account_id), account)


def get_account_posting_history(account_id: str, limit: int = 50) -> list[dict]:
    """Get the posting history for an account."""
    history_file = account_dir(account_id) / "posting-history.jsonl"
    if not history_file.exists():
        return []

    history = []
    try:
        with open(history_file) as f:
            for line in f.readlines()[-limit:]:
                history.append(json.loads(line))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Failed to load posting history for %s: %s", account_id, e)
    return history


def record_post_attempt(
    account_id: str,
    media_type: str,
    success: bool,
    post_id: str | None = None,
    error_message: str | None = None,
) -> None:
    """Record a post attempt in the account's history."""
    history_file = account_dir(account_id) / "posting-history.jsonl"

    entry = {
        "timestamp": utcnow_iso(),
        "media_type": media_type,
        "success": success,
        "post_id": post_id,
        "error_message": error_message,
    }

    try:
        with open(history_file, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError as e:
        logger.warning("Failed to record post attempt for %s: %s", account_id, e)

    if success:
        update_account_last_used(account_id)
        increment_account_post_count(account_id)


# ── load/save helpers ──────────────────────────────────────────────────────


def _load_account_metadata(path: Path) -> AccountMetadata | None:
    """Load account metadata from file."""
    try:
        data = json.loads(path.read_text())
        return AccountMetadata(**data)
    except (OSError, json.JSONDecodeError, TypeError) as e:
        logger.warning("Failed to load account metadata from %s: %s", path, e)
        return None


def _write_account_metadata(path: Path, metadata: AccountMetadata) -> None:
    """Write account metadata to file."""
    secure_write_text(path, json.dumps(asdict(metadata), indent=2, sort_keys=True) + "\n")


def _load_account_cookies(path: Path) -> dict[str, str] | None:
    """Load account cookies from file."""
    try:
        data = json.loads(path.read_text())
        if isinstance(data, dict):
            if "cookies" in data and isinstance(data["cookies"], list):
                return {
                    c["name"]: c["value"]
                    for c in data["cookies"]
                    if isinstance(c, dict) and "name" in c and "value" in c
                }
            return {k: v for k, v in data.items() if k != "imported_from"}
        if isinstance(data, list):
            return {
                c["name"]: c["value"]
                for c in data
                if isinstance(c, dict) and "name" in c and "value" in c
            }
        return None
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Failed to load cookies from %s: %s", path, e)
        return None


def _write_account_cookies(path: Path, cookies: dict[str, str]) -> None:
    """Write account cookies to file."""
    secure_write_text(path, json.dumps(cookies, indent=2, sort_keys=True) + "\n")
