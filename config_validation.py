"""Validation helpers for object animation configuration."""

from __future__ import annotations

from numbers import Real
from typing import Any

VALID_MODES = {"path", "out_and_back", "teleport_loop", "spin"}
REQUIRED_WAYPOINT_COORDS = ("x", "y", "z")
OPTIONAL_WAYPOINT_NUMBERS = ("rot_x", "rot_y", "rot_z")


def _is_numeric(value: Any) -> bool:
    """Return True for int/float values, excluding booleans."""
    return isinstance(value, Real) and not isinstance(value, bool)


def validate_object_config(objects: list[dict]) -> list[str]:
    """Return validation error messages for object animation definitions."""
    errors: list[str] = []

    if not isinstance(objects, list):
        return ["objects must be a list"]

    for index, obj in enumerate(objects):
        label = f"object #{index + 1}"
        if not isinstance(obj, dict):
            errors.append(f"{label} must be an object")
            continue

        name = obj.get("name")
        if not isinstance(name, str):
            errors.append(f"{label} name must be a string")
        elif name:
            label = f"object '{name}'"

        search_coords = obj.get("search_coords")
        if not isinstance(search_coords, list) or len(search_coords) != 3:
            errors.append(f"{label} search_coords must be a list with exactly 3 entries")
        else:
            for coord_index, coord in enumerate(search_coords):
                if not isinstance(coord, int) or isinstance(coord, bool):
                    errors.append(f"{label} search_coords[{coord_index}] must be an integer")

        mode = obj.get("mode")
        if "start_delay_seconds" in obj:
            delay = obj["start_delay_seconds"]
            if not _is_numeric(delay) or delay < 0:
                errors.append(f"{label} start_delay_seconds must be a non-negative number")

        if mode not in VALID_MODES:
            errors.append(f"{label} mode must be one of {sorted(VALID_MODES)}")
            continue

        if mode in {"path", "out_and_back", "teleport_loop"}:
            _validate_waypoints(obj.get("waypoints"), label, errors)
        elif mode == "spin":
            _validate_spin_rate(obj.get("spin_rate_deg_per_sec"), label, errors)

    return errors


def _validate_waypoints(waypoints: Any, label: str, errors: list[str]) -> None:
    if not isinstance(waypoints, list) or not waypoints:
        errors.append(f"{label} waypoints must be a non-empty list")
        return

    for index, waypoint in enumerate(waypoints):
        waypoint_label = f"{label} waypoint #{index + 1}"
        if not isinstance(waypoint, dict):
            errors.append(f"{waypoint_label} must be an object")
            continue

        for key in REQUIRED_WAYPOINT_COORDS:
            if key not in waypoint:
                errors.append(f"{waypoint_label} {key} is required")
            elif not _is_numeric(waypoint[key]):
                errors.append(f"{waypoint_label} {key} must be numeric")

        for key in OPTIONAL_WAYPOINT_NUMBERS:
            if key in waypoint and not _is_numeric(waypoint[key]):
                errors.append(f"{waypoint_label} {key} must be numeric")

        if "speed_mph" in waypoint:
            speed = waypoint["speed_mph"]
            if not _is_numeric(speed) or speed <= 0:
                errors.append(f"{waypoint_label} speed_mph must be a positive number")


def _validate_spin_rate(spin_rate: Any, label: str, errors: list[str]) -> None:
    if not isinstance(spin_rate, list) or len(spin_rate) != 3:
        errors.append(f"{label} spin_rate_deg_per_sec must be a list with exactly 3 numeric values")
        return

    for index, value in enumerate(spin_rate):
        if not _is_numeric(value):
            errors.append(f"{label} spin_rate_deg_per_sec[{index}] must be numeric")
