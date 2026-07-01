"""Deprecated compatibility launcher for the ICR2 Animator GUI.

The project now uses :mod:`icr2_launcher` as the primary UI because it can edit
objects, add/remove objects, save configs, and edit waypoint tables in one
place. Run normal sessions with::

    python icr2_launcher.py

This module remains only as a compatibility entry point for existing scripts or
shortcuts that still invoke ``python ui_launcher.py``.
"""

from __future__ import annotations

from icr2_launcher import main


if __name__ == "__main__":
    main()
