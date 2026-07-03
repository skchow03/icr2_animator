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

from icr2_logging import log_error, log_info, log_warn

from icr2_object_animator import ICR2ObjectAnimator
from icr2_versions import DEFAULT_ICR2_VERSION, ICR2_VERSION_CONFIGS, normalize_version


class AnimatorService:
    """Coordinate object animation runtime state and worker threads."""

    def __init__(
        self,
        version: str = DEFAULT_ICR2_VERSION,
        verbose: bool = True,
        fps: float = 60,
        window_keywords: list[str] | tuple[str, ...] | None = None,
    ):
        self.version = normalize_version(version)
        self.verbose = verbose
        self.fps = fps
        self.window_keywords = (
            tuple(window_keywords) if window_keywords is not None else None
        )
        self.animator: ICR2ObjectAnimator | None = None
        self.threads: list[threading.Thread] = []
        self._active_objects: list[
            tuple[str, int, tuple[int, int, int, int, int, int]]
        ] = []
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._stopping = False
        self._stopped = True

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
            if self._stopping:
                raise RuntimeError("AnimatorService is stopping")
            if self.is_running():
                raise RuntimeError("AnimatorService is already running")
            self._stopping = False
            self._stopped = False
            self._stop_event.clear()
            self.threads = []
            self._active_objects = []
            if self.verbose:
                log_info(
                    "Main",
                    f"Starting animation: version={self.version}, fps={self.fps:g}, objects={len(objects)}",
                )
            self.animator = ICR2ObjectAnimator(
                version=self.version,
                verbose=self.verbose,
                fps=self.fps,
                window_keywords=self.window_keywords,
            )
            self.animator.connect(self.window_keywords)

            started_count = 0
            for obj in objects:
                if self._start_object_animation(obj):
                    started_count += 1
            if self.verbose:
                if started_count:
                    log_info(
                        "Main",
                        f"Started {started_count}/{len(objects)} configured object(s).",
                    )
                else:
                    log_warn("Main", "No configured objects were started.")

    def stop(self):
        """Signal animation loops to stop and release DOSBox resources."""
        with self._lock:
            if self._stopping or self._stopped:
                return
            self._stopping = True
            self._stop_event.set()
            threads = list(self.threads)

        if self.verbose:
            log_info("Main", "Stop requested.")
        if self.verbose and threads:
            log_info(
                "Main", f"Waiting for {len(threads)} animation thread(s) to exit..."
            )
        deadline = time.monotonic() + 2.0
        for thread in threads:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            thread.join(timeout=remaining)
        for thread in threads:
            if thread.is_alive():
                log_warn(
                    "Main",
                    f"Animation thread {thread.name!r} did not exit before the shutdown deadline.",
                )

        with self._lock:
            self._reset_active_objects()

            if self.animator:
                self.animator.disconnect()
                self.animator = None

            self.threads = []
            self._active_objects = []
            self._stopping = False
            self._stopped = True
        if self.verbose:
            log_info("Main", "Stop complete.")

    def is_running(self) -> bool:
        """Return True while at least one tracked animation thread is alive."""
        return any(thread.is_alive() for thread in self.threads)

    def wait(self, poll_interval: float = 1.0):
        """Block until DOSBox closes, all animations exit, or stop() is requested."""
        try:
            while not self._stop_event.is_set():
                animator = self.animator
                if animator and not animator.is_alive():
                    log_warn("Main", "DOSBox closed, shutting down animator.")
                    break
                if not self.is_running():
                    log_info(
                        "Main", "All animation threads exited, shutting down animator."
                    )
                    break
                time.sleep(poll_interval)
        finally:
            self.stop()

    def _start_object_animation(self, obj: dict[str, Any]) -> bool:
        if not self.animator:
            raise RuntimeError("Animator is not connected")

        name = obj["name"]
        search_coords = tuple(obj["search_coords"])
        if self.verbose:
            log_info(
                "Animator", f"Searching for {name!r} at search_coords={search_coords}."
            )
        rel_addr = self.animator.find_coordinates_bulk(
            search_coords, ICR2_VERSION_CONFIGS[self.animator.version].object_search_range
        )
        if rel_addr is None:
            log_warn(
                "Animator", f"{name!r} not found at search_coords={search_coords}."
            )
            return False

        start_vals = self.animator.read_object6(rel_addr)
        animation_start_vals = self._animation_start_values(
            start_vals, obj.get("start_position")
        )
        mode = obj["mode"]
        self._active_objects.append((name, rel_addr, start_vals))
        if self.verbose:
            abs_addr = self.animator.memory.exe_base + rel_addr
            log_info(
                "Animator",
                f"Found {name!r} at rel=0x{rel_addr:X}, abs=0x{abs_addr:X}, start_values={start_vals}.",
            )
            if animation_start_vals != start_vals:
                log_info(
                    "Animator",
                    f"Teleporting {name!r} from captured start values to configured start_position={animation_start_vals}.",
                )

        if mode == "ping_pong_path":
            target = self.animator.animate_ping_pong_path
            args = (
                rel_addr,
                animation_start_vals,
                obj["waypoints"],
                obj["name"],
                self._stop_event,
            )
        elif mode == "return_to_start":
            target = self.animator.animate_return_to_start
            args = (
                rel_addr,
                animation_start_vals,
                obj["waypoints"],
                obj["name"],
                self._stop_event,
            )
        elif mode == "reset_loop":
            target = self.animator.animate_reset_loop
            args = (
                rel_addr,
                animation_start_vals,
                obj["waypoints"],
                obj["name"],
                self._stop_event,
            )
        elif mode == "rotate_in_place":
            target = self.animator.animate_rotate_in_place
            args = (
                rel_addr,
                animation_start_vals,
                tuple(obj["spin_rate_deg_per_sec"]),
                obj["name"],
                self._stop_event,
            )
        else:
            log_error("Animator", f"Unknown mode {mode!r} for {name!r}.")
            return False

        if animation_start_vals != start_vals:
            try:
                self.animator.write_object6(rel_addr, animation_start_vals)
            except SystemExit:
                log_error(
                    "Animator",
                    f"Could not teleport {name!r} to configured start_position; DOSBox is no longer available.",
                )
                return False

        start_delay_seconds = float(obj.get("start_delay_seconds", 0) or 0)
        thread = threading.Thread(
            target=self._run_after_start_delay,
            args=(start_delay_seconds, target, args, obj["name"]),
            daemon=True,
        )
        thread.start()
        self.threads.append(thread)
        if self.verbose:
            if mode == "rotate_in_place":
                detail = f"spin_rate={tuple(obj['spin_rate_deg_per_sec'])}"
            else:
                detail = f"waypoints={len(obj['waypoints'])}"
            log_info(
                "Animator",
                f"Started {name!r}: mode={mode}, {detail}, rel=0x{rel_addr:X}, delay={start_delay_seconds:g}s.",
            )
        return True

    def _animation_start_values(
        self,
        memory_start: tuple[int, int, int, int, int, int],
        start_position: Any,
    ) -> tuple[int, int, int, int, int, int]:
        """Return the animation start values after applying an optional start_position override."""
        if not isinstance(start_position, dict):
            return memory_start

        values = list(memory_start)
        for index, key in enumerate(("x", "y", "z")):
            if key in start_position:
                values[index] = int(start_position[key])

        if self.animator:
            for index, key in enumerate(("rot_x", "rot_y", "rot_z"), start=3):
                if key in start_position:
                    values[index] = self.animator.degrees_to_units(
                        float(start_position[key])
                    )

        return tuple(values)

    def _run_after_start_delay(
        self, delay_seconds: float, target, args: tuple, name: str
    ) -> None:
        """Wait for an object's configured start delay, then run its animation loop."""
        if delay_seconds > 0:
            if self.verbose:
                log_info(
                    "Animator", f"Waiting {delay_seconds:g}s before starting {name!r}."
                )
            if self._stop_event.wait(delay_seconds):
                if self.verbose:
                    log_info(
                        "Animator",
                        f"Start delay cancelled for {name!r} because animation was stopped.",
                    )
                return
            if self.verbose:
                log_info("Animator", f"Delay complete; starting {name!r}.")
        target(*args)
        if self.verbose and self._stop_event.is_set():
            log_info("Animator", f"{name!r} exited because stop was requested.")

    def _reset_active_objects(self) -> None:
        """Restore every discovered object to the coordinates captured at start()."""
        animator = self.animator
        if not animator or not animator.is_alive():
            return

        if self.verbose and self._active_objects:
            log_info(
                "Main",
                f"Restoring {len(self._active_objects)} object(s) to captured start values...",
            )

        for name, rel_addr, start_vals in self._active_objects:
            try:
                animator.write_object6(rel_addr, start_vals)
                if self.verbose:
                    log_info(
                        "Main",
                        f"Restored {name!r} to start values at rel=0x{rel_addr:X}.",
                    )
            except SystemExit:
                log_error(
                    "Main",
                    f"Failed to restore {name!r} at rel=0x{rel_addr:X}; DOSBox is no longer available.",
                )
                return
