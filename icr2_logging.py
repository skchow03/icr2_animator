"""Lightweight timestamped console logging helpers for ICR2 tools."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

LogLevel = Literal["DEBUG", "INFO", "WARN", "ERROR"]


def log_message(level: LogLevel, component: str, message: str) -> None:
    """Print a consistently formatted, timestamped log message."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{level}] [{component}] {message}")


def log_debug(component: str, message: str) -> None:
    log_message("DEBUG", component, message)


def log_info(component: str, message: str) -> None:
    log_message("INFO", component, message)


def log_warn(component: str, message: str) -> None:
    log_message("WARN", component, message)


def log_error(component: str, message: str) -> None:
    log_message("ERROR", component, message)
