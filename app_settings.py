"""Application-level INI settings for the ICR2 Animator launcher."""

from __future__ import annotations

import configparser
import sys
from pathlib import Path
from typing import Iterable

from icr2_versions import (
    DEFAULT_ICR2_VERSION,
    ICR2_VERSION_CONFIGS,
    normalize_version,
)

LAUNCHER_SECTION = "launcher"
WINDOW_KEYWORDS_SECTION = "window_keywords"
DEFAULT_CONFIG_PATH = "objects.json"
DEFAULT_FPS = "60"
DEFAULT_TOOLTIPS_ENABLED = False


def default_settings_path() -> Path:
    """Return the INI path beside the active program entry point."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().with_suffix(".ini")
    return Path(sys.argv[0]).resolve().with_suffix(".ini")


class AppSettings:
    """Read and write launcher settings stored in a small INI file."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_settings_path()
        self.config = configparser.ConfigParser()
        self.load()

    def load(self) -> None:
        """Load settings from disk if the INI file exists.

        Missing or invalid files are treated like empty settings so callers can
        rely on their built-in defaults.
        """
        self.config.clear()
        try:
            self.config.read(self.path)
        except (OSError, configparser.Error, UnicodeDecodeError):
            self.config.clear()

    def selected_version(self, default: str = DEFAULT_ICR2_VERSION) -> str:
        """Return the configured ICR2 version or a safe default."""
        value = self.config.get(LAUNCHER_SECTION, "version", fallback=default)
        try:
            return normalize_version(value)
        except ValueError:
            return default

    def config_path(self, default: str = DEFAULT_CONFIG_PATH) -> str:
        """Return the configured JSON object config path."""
        value = self.config.get(LAUNCHER_SECTION, "config_path", fallback=default)
        return value.strip() or default

    def fps(self, default: str = DEFAULT_FPS) -> str:
        """Return the configured FPS string if it is positive, otherwise default."""
        value = self.config.get(LAUNCHER_SECTION, "fps", fallback=default).strip()
        try:
            if float(value) <= 0:
                raise ValueError
        except ValueError:
            return default
        return value

    def tooltips_enabled(self, default: bool = DEFAULT_TOOLTIPS_ENABLED) -> bool:
        """Return whether launcher tooltips are enabled."""
        try:
            return self.config.getboolean(
                LAUNCHER_SECTION, "tooltips_enabled", fallback=default
            )
        except ValueError:
            return default

    def set_launcher_settings(
        self,
        *,
        version: str,
        config_path: str,
        fps: str,
        tooltips_enabled: bool,
    ) -> None:
        """Store launcher-level settings without object animation definitions."""
        if not self.config.has_section(LAUNCHER_SECTION):
            self.config.add_section(LAUNCHER_SECTION)
        self.config.set(LAUNCHER_SECTION, "version", normalize_version(version))
        self.config.set(LAUNCHER_SECTION, "config_path", config_path.strip())
        self.config.set(LAUNCHER_SECTION, "fps", fps.strip())
        self.config.set(
            LAUNCHER_SECTION, "tooltips_enabled", "yes" if tooltips_enabled else "no"
        )

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
        """Store window keywords for a version without saving immediately."""
        normalized = normalize_version(version)
        keyword_list = normalize_window_keywords(keywords)
        if not self.config.has_section(WINDOW_KEYWORDS_SECTION):
            self.config.add_section(WINDOW_KEYWORDS_SECTION)
        self.config.set(WINDOW_KEYWORDS_SECTION, normalized, ", ".join(keyword_list))

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
