"""Section config dicts for Facebook scraping surfaces."""

import logging

logger = logging.getLogger(__name__)

# Profile/page sections - each maps to one Facebook URL
PROFILE_SECTIONS: dict[str, tuple[str, bool]] = {
    "main_profile": ("/", False),
    "posts": ("/", False),
    "about": ("/about", False),
    "photos": ("/photos", False),
}


def parse_profile_sections(
    sections: str | None,
) -> tuple[set[str], list[str]]:
    """Parse comma-separated section names into a set of requested sections.

    "main_profile" is always included. Empty/None returns {"main_profile"} only.
    Unknown section names are logged as warnings and returned.

    Returns:
        Tuple of (requested_sections, unknown_section_names).
    """
    requested: set[str] = {"main_profile"}
    unknown: list[str] = []
    if not sections:
        return requested, unknown
    for name in sections.split(","):
        name = name.strip().lower()
        if not name:
            continue
        if name in PROFILE_SECTIONS:
            requested.add(name)
        else:
            unknown.append(name)
            logger.warning(
                "Unknown profile section %r ignored. Valid: %s",
                name,
                ", ".join(sorted(PROFILE_SECTIONS)),
            )
    return requested, unknown
