"""Known ICR2 executable/window-title version identifiers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ICR2VersionConfig:
    """Memory discovery settings for a supported ICR2 executable version."""

    window_keywords: tuple[str, ...]
    signature_bytes: bytes
    signature_offset: int


_LICENSE_WITH_BOB_SIGNATURE = bytes.fromhex(
    "6C 69 63 65 6E 73 65 20 77 69 74 68 20 42 6F 62"
)


ICR2_VERSION_CONFIGS: dict[str, ICR2VersionConfig] = {
    "REND32A": ICR2VersionConfig(
        window_keywords=("dosbox", "cart"),
        signature_bytes=_LICENSE_WITH_BOB_SIGNATURE,
        signature_offset=int("B1C0C", 16),
    ),
    "DOS": ICR2VersionConfig(
        window_keywords=("dosbox", "indycar"),
        signature_bytes=_LICENSE_WITH_BOB_SIGNATURE,
        signature_offset=int("A0D78", 16),
    ),
    "WINDY": ICR2VersionConfig(
        window_keywords=("cart racing"),
        signature_bytes=_LICENSE_WITH_BOB_SIGNATURE,
        signature_offset=int("A0D78", 16),
    ),
}


KNOWN_ICR2_VERSIONS: tuple[str, ...] = tuple(ICR2_VERSION_CONFIGS.keys())
DEFAULT_ICR2_VERSION = "REND32A"


def normalize_version(version: str) -> str:
    """Return a canonical version identifier or raise ValueError if unsupported."""
    normalized = version.upper()
    if normalized not in ICR2_VERSION_CONFIGS:
        supported = ", ".join(KNOWN_ICR2_VERSIONS)
        raise ValueError(f"version must be one of: {supported}")
    return normalized
