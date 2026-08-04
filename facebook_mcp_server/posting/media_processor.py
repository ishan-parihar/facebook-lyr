"""
Media processor for Facebook posting.

Handles image preprocessing for Facebook's specifications (resize to max
dimensions, normalize to a feed-valid aspect ratio, compress, save as JPEG).
Video processing is a lightweight pass-through (duration/size checks are
best-effort) — heavy transcode work is left to the client since Facebook's
web composer accepts standard mp4 uploads.

Mirrors instagram-lyr's ``posting/media_processor.py``.
"""

from __future__ import annotations

import logging
import tempfile
import time
from pathlib import Path

logger = logging.getLogger(__name__)


class MediaProcessor:
    """Process images for Facebook posting specifications."""

    # Facebook specifications (web composer, 2025)
    MAX_IMAGE_SIZE = 2048  # pixels on the longest side
    FEED_ASPECT_RATIO = (1, 1)  # square default; kept when source is square-ish
    IMAGE_QUALITY = 85  # JPEG quality (0-100)

    @staticmethod
    def process_image(
        image_path: str,
        target_aspect: tuple[int, int] | None = None,
        max_size: int = MAX_IMAGE_SIZE,
        force_aspect: bool = False,
    ) -> str:
        """Process an image for Facebook (resize, compress, JPEG).

        Args:
            image_path: Path to image file
            target_aspect: Target aspect ratio (width, height). If None, preserves
                the source aspect ratio (within Facebook's feed range).
            max_size: Maximum dimension in pixels
            force_aspect: If True, crop to the target aspect ratio; otherwise the
                aspect ratio is preserved and only dimensions are capped.

        Returns:
            Path to the processed image file (JPEG)

        Raises:
            ImportError: If Pillow is not installed
            Exception: If image processing fails
        """
        from PIL import Image

        img = Image.open(image_path)

        # Convert to RGB if necessary (for JPEG output)
        if img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")

        # Normalize aspect ratio if requested
        if target_aspect and force_aspect:
            img = MediaProcessor._crop_to_aspect(img, target_aspect)

        # Cap the longest side
        img = MediaProcessor._cap_dimensions(img, max_size)

        # Save to temp file
        temp_path = MediaProcessor._save_temp_image(img, image_path)
        logger.info("Processed image: %s -> %s", image_path, temp_path)
        return temp_path

    @staticmethod
    def process_image_smart(
        image_path: str,
        fit_mode: str = "auto",
    ) -> str:
        """Process an image with smart handling of extreme aspect ratios.

        Facebook feed accepts roughly 1.91:1 down to 4:5. Images outside that
        range are cropped (or letterboxed) into the nearest valid ratio so the
        upload isn't rejected for its geometry.

        Args:
            image_path: Path to image file
            fit_mode: "auto" (crop extreme ratios), "crop" (always crop to 1:1),
                "preserve" (never change geometry, only cap dimensions)

        Returns:
            Path to the processed image file

        Raises:
            ImportError: If Pillow is not installed
            Exception: If image processing fails
        """
        from PIL import Image

        img = Image.open(image_path)

        if img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")

        width, height = img.size
        if width > 0 and height > 0:
            ratio = width / height
            if fit_mode == "crop":
                img = MediaProcessor._crop_to_aspect(img, (1, 1))
            elif fit_mode == "auto" and not (0.8 <= ratio <= 1.91):
                target = (16, 9) if ratio > 1.91 else (4, 5)
                img = MediaProcessor._crop_to_aspect(img, target)

        img = MediaProcessor._cap_dimensions(img, MediaProcessor.MAX_IMAGE_SIZE)

        temp_path = MediaProcessor._save_temp_image(img, image_path)
        logger.info("Smart processed image: %s -> %s (mode: %s)", image_path, temp_path, fit_mode)
        return temp_path

    @staticmethod
    def _crop_to_aspect(img, target_aspect: tuple[int, int]):
        """Center-crop an image to the target aspect ratio."""
        width, height = img.size
        target_w, target_h = target_aspect
        target_ratio = target_w / target_h
        current_ratio = width / height

        if current_ratio > target_ratio:
            # too wide — crop width
            new_width = int(height * target_ratio)
            left = (width - new_width) // 2
            return img.crop((left, 0, left + new_width, height))
        if current_ratio < target_ratio:
            # too tall — crop height
            new_height = int(width / target_ratio)
            top = (height - new_height) // 2
            return img.crop((0, top, width, top + new_height))
        return img

    @staticmethod
    def _cap_dimensions(img, max_size: int):
        """Scale an image so its longest side is at most max_size."""
        from PIL import Image

        width, height = img.size
        if max(width, height) > max_size:
            if width >= height:
                new_width = max_size
                new_height = int(height * (max_size / width))
            else:
                new_height = max_size
                new_width = int(width * (max_size / height))
            return img.resize((new_width, new_height), Image.Resampling.LANCZOS)
        return img

    @staticmethod
    def _save_temp_image(img, source_path: str) -> str:
        """Save an image to a unique temp JPEG file."""
        temp_dir = Path(tempfile.gettempdir()) / "facebook-media"
        temp_dir.mkdir(exist_ok=True)
        stem = Path(source_path).stem.replace(" ", "_")[:40] or "image"
        temp_path = temp_dir / f"fb_{stem}_{int(time.time() * 1000)}.jpg"
        img.save(temp_path, "JPEG", quality=MediaProcessor.IMAGE_QUALITY, optimize=True)
        return str(temp_path)

    @staticmethod
    def validate_image(image_path: str) -> bool:
        """Validate that an image opens and is non-empty.

        Args:
            image_path: Path to image file

        Returns:
            True if valid, False otherwise
        """
        try:
            from PIL import Image

            img = Image.open(image_path)
            img.verify()
            return True
        except Exception:
            return False

    @staticmethod
    def video_metadata(video_path: str) -> dict:
        """Best-effort video metadata (duration, size) without transcoding.

        Uses ``ffprobe`` when available; otherwise returns an empty dict so the
        caller can proceed (the composer enforces its own limits).

        Args:
            video_path: Path to video file

        Returns:
            Dict with duration (s), width, height, size_bytes when detectable
        """
        import shutil
        import subprocess

        probe = shutil.which("ffprobe")
        if not probe:
            return {}
        try:
            result = subprocess.run(
                [
                    probe,
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration,size:stream=width,height",
                    "-of",
                    "json",
                    video_path,
                ],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            if result.returncode != 0:
                return {}
            import json

            data = json.loads(result.stdout or "{}")
            fmt = data.get("format", {})
            streams = data.get("streams", [])
            meta: dict = {}
            if fmt.get("duration"):
                try:
                    meta["duration"] = float(fmt["duration"])
                except (TypeError, ValueError):
                    pass
            if fmt.get("size"):
                try:
                    meta["size_bytes"] = int(fmt["size"])
                except (TypeError, ValueError):
                    pass
            for stream in streams:
                if stream.get("width") and stream.get("height"):
                    meta["width"] = int(stream["width"])
                    meta["height"] = int(stream["height"])
                    break
            return meta
        except Exception:
            logger.debug("ffprobe metadata failed for %s", video_path, exc_info=True)
            return {}
