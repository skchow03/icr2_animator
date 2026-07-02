"""Application-level INI settings for the ICR2 Animator launcher."""

from __future__ import annotations

import configparser
import sys
from pathlib import Path
from typing import Iterable

from icr2_versions import ICR2_VERSION_CONFIGS, normalize_version

WINDOW_KEYWORDS_SECTION = "window_keywords"


def default_settings_path() -> Path:
    """Return the INI path beside the active program entry point."""
    return Path(sys.argv[0]).resolve().with_suffix(".ini")


class AppSettings:
    """Read and write launcher settings stored in a small INI file."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_settings_path()
        self.config = configparser.ConfigParser()
        self.load()

    def load(self) -> None:
        """Load settings from disk if the INI file exists."""
        self.config.clear()
        self.config.read(self.path)

    def window_keywords_for_version(self, version: str) -> tuple[str, ...]:
        """Return configured window keywords or the built-in default for a version."""
        normalized = normalize_version(version)
        value = self.config.get(WINDOW_KEYWORDS_SECTION, normalized, fallback="")
        keywords = parse_window_keywords(value)
        if keywords:
            return tuple(keywords)
        return ICR2_VERSION_CONFIGS[normalized].window_keywords

    def set_window_keywords_for_version(
        self, version: str, keywords: Iterable[str]
    ) -> None:
        """Store window keywords for a version and write the INI file."""
        normalized = normalize_version(version)
        keyword_list = normalize_window_keywords(keywords)
        if not self.config.has_section(WINDOW_KEYWORDS_SECTION):
            self.config.add_section(WINDOW_KEYWORDS_SECTION)
        self.config.set(WINDOW_KEYWORDS_SECTION, normalized, ", ".join(keyword_list))
        self.save()

    def save(self) -> None:
        """Write settings to disk, creating the application directory when needed."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as settings_file:
            self.config.write(settings_file)


def parse_window_keywords(text: str) -> list[str]:
    """Split comma-separated keyword text, trimming whitespace and dropping blanks."""
    return [part.strip() for part in text.split(",") if part.strip()]


def normalize_window_keywords(keywords: Iterable[str]) -> list[str]:
    """Normalize a keyword iterable using the same rules as text entry input."""
    normalized: list[str] = []
    for keyword in keywords:
        normalized.extend(parse_window_keywords(str(keyword)))
    return normalized
