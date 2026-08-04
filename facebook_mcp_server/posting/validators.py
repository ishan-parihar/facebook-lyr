"""
Media validators for Facebook posting.

Validates status text, media files, and posting parameters to ensure they
meet Facebook's requirements before any transport attempt — mirroring
instagram-lyr's ``posting/validators.py``.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class PostingValidator:
    """Validate posting parameters and media for Facebook."""

    # Facebook specifications (web composer)
    MAX_STATUS_LENGTH = 63206  # characters (Facebook's hard status limit)
    MAX_CAPTION_LENGTH = 63206  # characters for photo/video captions
    MAX_IMAGE_SIZE = 2048  # pixels on the longest side
    MAX_CAROUSEL_ITEMS = 10
    MIN_CAROUSEL_ITEMS = 2

    VALID_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
    VALID_VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".webm", ".mkv"}

    # Valid feed aspect ratios (width/height ranges Facebook accepts)
    # 1.91:1 (landscape) .. 4:5 (portrait); 1:1 square in between.
    FEED_MIN_RATIO = 0.8  # 4:5
    FEED_MAX_RATIO = 1.91  # 1.91:1

    @staticmethod
    def validate_status(status: str) -> tuple[bool, str]:
        """Validate post status text.

        Args:
            status: Status text to publish

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not status or not status.strip():
            return False, "Status text cannot be empty"

        if len(status) > PostingValidator.MAX_STATUS_LENGTH:
            return (
                False,
                f"Status too long: {len(status)} characters "
                f"(max {PostingValidator.MAX_STATUS_LENGTH})",
            )

        return True, ""

    @staticmethod
    def validate_caption(caption: str) -> tuple[bool, str]:
        """Validate a photo/video caption (empty captions are allowed).

        Args:
            caption: Caption text

        Returns:
            Tuple of (is_valid, error_message)
        """
        if len(caption) > PostingValidator.MAX_CAPTION_LENGTH:
            return (
                False,
                f"Caption too long: {len(caption)} characters "
                f"(max {PostingValidator.MAX_CAPTION_LENGTH})",
            )
        return True, ""

    @staticmethod
    def validate_comment(text: str) -> tuple[bool, str]:
        """Validate comment / Messenger message text.

        Args:
            text: Comment or message text

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not text or not text.strip():
            return False, "Text cannot be empty"
        if len(text) > 2000:
            return (
                False,
                f"Text too long: {len(text)} characters (max 2000)",
            )
        return True, ""

    @staticmethod
    def validate_photo_path(image_path: str) -> tuple[bool, str]:
        """Validate a photo path and file.

        Args:
            image_path: Path to image file

        Returns:
            Tuple of (is_valid, error_message)
        """
        path = Path(image_path).expanduser()

        if not path.exists():
            return False, f"Image file not found: {image_path}"

        if not path.is_file():
            return False, f"Path is not a file: {image_path}"

        if path.suffix.lower() not in PostingValidator.VALID_IMAGE_EXTENSIONS:
            return (
                False,
                f"Invalid image format: {path.suffix}. "
                f"Supported: {sorted(PostingValidator.VALID_IMAGE_EXTENSIONS)}",
            )

        return True, ""

    @staticmethod
    def validate_video_path(video_path: str) -> tuple[bool, str]:
        """Validate a video path and file.

        Args:
            video_path: Path to video file

        Returns:
            Tuple of (is_valid, error_message)
        """
        path = Path(video_path).expanduser()

        if not path.exists():
            return False, f"Video file not found: {video_path}"

        if not path.is_file():
            return False, f"Path is not a file: {video_path}"

        if path.suffix.lower() not in PostingValidator.VALID_VIDEO_EXTENSIONS:
            return (
                False,
                f"Invalid video format: {path.suffix}. "
                f"Supported: {sorted(PostingValidator.VALID_VIDEO_EXTENSIONS)}",
            )

        return True, ""

    @staticmethod
    def validate_carousel_paths(paths: list[str]) -> tuple[bool, str]:
        """Validate a carousel's media paths.

        Args:
            paths: List of media file paths

        Returns:
            Tuple of (is_valid, error_message)
        """
        if len(paths) < PostingValidator.MIN_CAROUSEL_ITEMS:
            return (
                False,
                f"Carousel must have at least {PostingValidator.MIN_CAROUSEL_ITEMS} "
                f"media items (got {len(paths)})",
            )

        if len(paths) > PostingValidator.MAX_CAROUSEL_ITEMS:
            return (
                False,
                f"Carousel can have at most {PostingValidator.MAX_CAROUSEL_ITEMS} "
                f"media items (got {len(paths)})",
            )

        for path in paths:
            valid, msg = PostingValidator.validate_photo_path(path)
            if not valid:
                return False, f"Invalid carousel item: {msg}"

        return True, ""

    @staticmethod
    def validate_media_source(source: str) -> tuple[bool, str]:
        """Validate that a media source is a local path or http(s) URL.

        Args:
            source: Local file path or http(s) URL

        Returns:
            Tuple of (is_valid, error_message)
        """
        if source.startswith(("http://", "https://")):
            return True, ""
        return PostingValidator.validate_photo_path(source)

    @staticmethod
    def validate_aspect_ratio(width: int, height: int) -> tuple[bool, str]:
        """Validate an image's aspect ratio is within Facebook's feed range.

        Args:
            width: Image width in pixels
            height: Image height in pixels

        Returns:
            Tuple of (is_valid, error_message)
        """
        if height <= 0:
            return False, "Height cannot be zero"

        ratio = width / height
        if not (PostingValidator.FEED_MIN_RATIO <= ratio <= PostingValidator.FEED_MAX_RATIO):
            return (
                False,
                f"Invalid aspect ratio {width}:{height} ({ratio:.2f}) for a feed post. "
                f"Accepted range: {PostingValidator.FEED_MIN_RATIO:.2f} to "
                f"{PostingValidator.FEED_MAX_RATIO:.2f} (4:5 .. 1.91:1)",
            )
        return True, ""

    @staticmethod
    def validate_account_id(account_id: str | None) -> tuple[bool, str]:
        """Validate an account ID.

        Args:
            account_id: Account ID to validate

        Returns:
            Tuple of (is_valid, error_message)
        """
        if account_id is None:
            return True, ""  # optional — active account is used
        if not account_id or not account_id.strip():
            return False, "Account ID cannot be empty"
        return True, ""
