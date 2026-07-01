"""
animator_service.py - Runtime orchestration for ICR2 object animations.

AnimatorService owns configuration loading, DOSBox connection lifecycle,
animation thread creation/tracking, and cooperative shutdown signaling.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any

from icr2_object_animator import ICR2ObjectAnimator
from icr2_versions import DEFAULT_ICR2_VERSION, normalize_version


class AnimatorService:
    """Coordinate object animation runtime state and worker threads."""

    def __init__(self, version: str = DEFAULT_ICR2_VERSION, verbose: bool = True, fps: float = 60):
        self.version = normalize_version(version)
        self.verbose = verbose
        self.fps = fps
        self.animator: ICR2ObjectAnimator | None = None
        self.threads: list[threading.Thread] = []
        self._active_objects: list[tuple[int, tuple[int, int, int, int, int, int]]] = []
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

    def load_objects(self, path: str) -> list[dict[str, Any]]:
        """Load object animation definitions from a JSON configuration file."""
        with open(path, "r") as f:
            return json.load(f)["objects"]

    def set_version(self, version: str):
        """Set the ICR2/DOSBox version used by future animation runs."""
        if self.is_running():
            raise RuntimeError("Cannot change version while animations are running")
        self.version = normalize_version(version)

    def start(self, objects: list[dict[str, Any]]):
        """Connect to DOSBox, discover configured objects, and start animations."""
        with self._lock:
            if self.is_running():
                raise RuntimeError("AnimatorService is already running")
            self._stop_event.clear()
            self.threads = []
            self._active_objects = []
            self.animator = ICR2ObjectAnimator(version=self.version, verbose=self.verbose, fps=self.fps)
            self.animator.connect()

            for obj in objects:
                self._start_object_animation(obj)

    def stop(self):
        """Signal animation loops to stop and release DOSBox resources."""
        self._stop_event.set()

        for thread in list(self.threads):
            thread.join(timeout=2)

        self._reset_active_objects()

        if self.animator:
            self.animator.disconnect()
            self.animator = None

        self.threads = []
        self._active_objects = []

    def is_running(self) -> bool:
        """Return True while at least one tracked animation thread is alive."""
        return any(thread.is_alive() for thread in self.threads)

    def wait(self, poll_interval: float = 1.0):
        """Block until DOSBox closes, all animations exit, or stop() is requested."""
        try:
            while not self._stop_event.is_set():
                animator = self.animator
                if animator and not animator.is_alive():
                    print("[Main] DOSBox closed, shutting down animator.")
                    break
                if not self.is_running():
                    print("[Main] All animation threads exited, shutting down animator.")
                    break
                time.sleep(poll_interval)
        finally:
            self.stop()

    def _start_object_animation(self, obj: dict[str, Any]):
        if not self.animator:
            raise RuntimeError("Animator is not connected")

        rel_addr = self.animator.find_coordinates_bulk(
            tuple(obj["search_coords"]), (0, 0xF0000000)
        )
        if rel_addr is None:
            print(f"[Animator] {obj['name']} not found.")
            return

        start_vals = self.animator.read_object6(rel_addr)
        mode = obj["mode"]
        self._active_objects.append((rel_addr, start_vals))

        if mode == "path":
            target = self.animator.animate_path
            args = (rel_addr, start_vals, obj["waypoints"], obj["name"], self._stop_event)
        elif mode == "out_and_back":
            target = self.animator.animate_out_and_back
            args = (rel_addr, start_vals, obj["waypoints"], obj["name"], self._stop_event)
        elif mode == "teleport_loop":
            target = self.animator.animate_teleport_loop
            args = (rel_addr, start_vals, obj["waypoints"], obj["name"], self._stop_event)
        elif mode == "spin":
            target = self.animator.animate_spin
            args = (
                rel_addr,
                start_vals,
                tuple(obj["spin_rate_deg_per_sec"]),
                obj["name"],
                self._stop_event,
            )
        else:
            print(f"[Animator] Unknown mode {mode} for {obj['name']}")
            return

        start_delay_seconds = float(obj.get("start_delay_seconds", 0) or 0)
        thread = threading.Thread(
            target=self._run_after_start_delay,
            args=(start_delay_seconds, target, args, obj["name"]),
            daemon=True,
        )
        thread.start()
        self.threads.append(thread)

    def _run_after_start_delay(self, delay_seconds: float, target, args: tuple, name: str) -> None:
        """Wait for an object's configured start delay, then run its animation loop."""
        if delay_seconds > 0:
            if self.verbose:
                print(f"[Animator] Waiting {delay_seconds:g}s before starting {name}.")
            if self._stop_event.wait(delay_seconds):
                return
        target(*args)

    def _reset_active_objects(self) -> None:
        """Restore every discovered object to the coordinates captured at start()."""
        animator = self.animator
        if not animator or not animator.is_alive():
            return

        for rel_addr, start_vals in self._active_objects:
            try:
                animator.write_object6(rel_addr, start_vals)
            except SystemExit:
                return
