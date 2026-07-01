"""
icr2_object_animator.py - Animate 3D objects in ICR2 by manipulating memory coordinates

Features:
- Multiple objects configured in objects.json
- Modes:
  * "path": forward & backward through waypoints (returns to start before repeating)
  * "out_and_back": start → waypoints → return directly to start → repeat
  * "spin": spin in place about chosen axes
- Animations run in parallel threads
- Coordinates: x, y, z in 1/500 inch units
- Rotations: int32 wraparound covering 360 degrees, three axes (rotX, rotY, rotZ)

All memory addresses are relative to exe_base (as in Ghidra/notes).
"""

import time
import math
import json
import struct
import threading
from typing import Tuple, List, Dict, Any
from icr2_memory import ICR2Memory


class ICR2ObjectAnimator:
    UNITS_PER_DEGREE = 4294967296 / 360.0  # 4-byte signed int covers 360°

    def __init__(self, version="REND32A", verbose=True):
        self.memory = None
        self.version = version
        self.verbose = verbose
        self.fps = 30
        self.frame_time = 1.0 / self.fps

    # ---------------- Connection ----------------
    def connect(self):
        self.memory = ICR2Memory(self.version, verbose=self.verbose)
        if self.verbose:
            print(f"[Animator] Connected ({self.version})")

    def disconnect(self):
        if self.memory:
            self.memory.close()
            self.memory = None
            if self.verbose:
                print("[Animator] Disconnected")

    def is_alive(self) -> bool:
        """Check if the DOSBox process is still alive."""
        if not self.memory or not self.memory.pm:
            return False
        try:
            return self.memory.pm.process_handle is not None
        except Exception:
            return False

    # ---------------- Read/Write ----------------
    def read_object6(self, rel_addr: int) -> Tuple[int, int, int, int, int, int]:
        """Read x,y,z, rotX, rotY, rotZ (6 ints)."""
        abs_addr = self.memory.exe_base + rel_addr
        data = self.memory.pm.read_bytes(abs_addr, 24)
        return struct.unpack('<iiiiii', data)

    def write_object6(self, rel_addr: int, values: Tuple[int, int, int, int, int, int]):
        """Write x,y,z, rotX, rotY, rotZ (6 ints), safe against process exit."""
        if not self.is_alive():
            raise SystemExit
        abs_addr = self.memory.exe_base + rel_addr
        data = struct.pack('<iiiiii', *values)
        try:
            self.memory.pm.write_bytes(abs_addr, data, len(data))
        except Exception as e:
            if self.verbose:
                print(f"[Animator] Write failed at 0x{abs_addr:X}: {e}")
            raise SystemExit

    # ---------------- Helpers ----------------
    def degrees_to_units(self, deg: float) -> int:
        return int(deg * self.UNITS_PER_DEGREE)

    def units_to_degrees(self, units: int) -> float:
        return units / self.UNITS_PER_DEGREE

    def mph_to_inches_per_sec(self, mph: float) -> float:
        return mph * 17.6  # 1 mph = 17.6 in/sec

    def distance_in_inches(self, a: Tuple[int, int, int], b: Tuple[int, int, int]) -> float:
        dx, dy, dz = [(b[i] - a[i]) / 500.0 for i in range(3)]
        return math.sqrt(dx * dx + dy * dy + dz * dz)

    # ---------------- Modes ----------------
    def animate_path(self, rel_addr: int, start: Tuple[int, int, int, int, int, int],
                     waypoints: List[Dict[str, Any]], name: str = "Object"):
        """Move object back and forth along waypoints, returning to original start."""
        start_wp = {
            "x": start[0], "y": start[1], "z": start[2],
            "rot_x": self.units_to_degrees(start[3]),
            "rot_y": self.units_to_degrees(start[4]),
            "rot_z": self.units_to_degrees(start[5]),
            "speed_mph": waypoints[0].get("speed_mph", 30) if waypoints else 30
        }
        full_wp = [start_wp] + waypoints
        current = start

        while True:
            if not self.is_alive():
                if self.verbose:
                    print(f"[{name}] DOSBox closed, exiting path loop.")
                return
            seq = full_wp + full_wp[-2:0:-1]  # forward + backward
            for wp in seq:
                target = (
                    wp["x"], wp["y"], wp["z"],
                    self.degrees_to_units(wp.get("rot_x", 0)),
                    self.degrees_to_units(wp.get("rot_y", 0)),
                    self.degrees_to_units(wp.get("rot_z", 0)),
                )
                dist = self.distance_in_inches(current[:3], target[:3])
                speed_ips = self.mph_to_inches_per_sec(wp.get("speed_mph", 30))
                duration = dist / speed_ips if speed_ips > 0 else 1.0
                total_frames = max(1, int(duration * self.fps))

                for f in range(total_frames + 1):
                    if not self.is_alive():
                        return
                    progress = f / total_frames
                    interp = tuple(int(current[i] + (target[i] - current[i]) * progress)
                                   for i in range(6))
                    try:
                        self.write_object6(rel_addr, interp)
                    except SystemExit:
                        return
                    if self.verbose and f % (self.fps // 2) == 0:
                        print(f"[{name}] Frame {f}/{total_frames} at {interp}")
                    time.sleep(self.frame_time)
                current = target

    def animate_out_and_back(self, rel_addr: int, start: Tuple[int, int, int, int, int, int],
                             waypoints: List[Dict[str, Any]], name: str = "Object"):
        """Animate: start → waypoints → return directly to start → repeat."""
        start_wp = {
            "x": start[0], "y": start[1], "z": start[2],
            "rot_x": self.units_to_degrees(start[3]),
            "rot_y": self.units_to_degrees(start[4]),
            "rot_z": self.units_to_degrees(start[5]),
            "speed_mph": waypoints[0].get("speed_mph", 30) if waypoints else 30
        }
        current = start

        while True:
            if not self.is_alive():
                if self.verbose:
                    print(f"[{name}] DOSBox closed, exiting out-and-back loop.")
                return

            # Forward pass through waypoints
            for wp in waypoints:
                target = (
                    wp["x"], wp["y"], wp["z"],
                    self.degrees_to_units(wp.get("rot_x", 0)),
                    self.degrees_to_units(wp.get("rot_y", 0)),
                    self.degrees_to_units(wp.get("rot_z", 0)),
                )
                dist = self.distance_in_inches(current[:3], target[:3])
                speed_ips = self.mph_to_inches_per_sec(wp.get("speed_mph", 30))
                duration = dist / speed_ips if speed_ips > 0 else 1.0
                total_frames = max(1, int(duration * self.fps))

                for f in range(total_frames + 1):
                    if not self.is_alive():
                        return
                    progress = f / total_frames
                    interp = tuple(int(current[i] + (target[i] - current[i]) * progress)
                                   for i in range(6))
                    try:
                        self.write_object6(rel_addr, interp)
                    except SystemExit:
                        return
                    time.sleep(self.frame_time)
                current = target

            # Return to start directly
            target = (
                start_wp["x"], start_wp["y"], start_wp["z"],
                self.degrees_to_units(start_wp.get("rot_x", 0)),
                self.degrees_to_units(start_wp.get("rot_y", 0)),
                self.degrees_to_units(start_wp.get("rot_z", 0)),
            )
            dist = self.distance_in_inches(current[:3], target[:3])
            speed_ips = self.mph_to_inches_per_sec(start_wp.get("speed_mph", 30))
            duration = dist / speed_ips if speed_ips > 0 else 1.0
            total_frames = max(1, int(duration * self.fps))

            for f in range(total_frames + 1):
                if not self.is_alive():
                    return
                progress = f / total_frames
                interp = tuple(int(current[i] + (target[i] - current[i]) * progress)
                               for i in range(6))
                try:
                    self.write_object6(rel_addr, interp)
                except SystemExit:
                    return
                time.sleep(self.frame_time)
            current = target

    def animate_spin(self, rel_addr: int, start: Tuple[int, int, int, int, int, int],
                     spin_rate: Tuple[float, float, float], name: str = "Object"):
        """Spin object in place forever."""
        pos = start[:3]
        rot = [self.units_to_degrees(r) for r in start[3:]]
        while True:
            if not self.is_alive():
                if self.verbose:
                    print(f"[{name}] DOSBox closed, exiting spin loop.")
                return
            for i in range(3):
                rot[i] += spin_rate[i] / self.fps
                rot[i] = ((rot[i] + 180) % 360) - 180
            rot_units = [self.degrees_to_units(r) for r in rot]
            try:
                self.write_object6(rel_addr, (*pos, *rot_units))
            except SystemExit:
                return
            time.sleep(self.frame_time)

    # ---------------- Search ----------------
    def find_coordinates_bulk(self, target_coords: Tuple[int, int, int],
                              search_range: Tuple[int, int],
                              chunk_size: int = 0x40000) -> int:
        """
        Fast search for (x,y,z) coordinates in memory.
        Returns relative offset or None if not found.
        """
        x_target, y_target, z_target = target_coords
        start_offset, end_offset = search_range
        pattern = struct.pack('<iii', x_target, y_target, z_target)

        if self.verbose:
            mb = (end_offset - start_offset) / (1024 * 1024)
            print(f"[Animator] Searching {mb:.1f} MB for {target_coords}")

        offset = start_offset
        overlap = 12

        while offset < end_offset:
            read_size = min(chunk_size, end_offset - offset)
            abs_addr = self.memory.exe_base + offset
            try:
                blob = self.memory.pm.read_bytes(abs_addr, read_size)
            except Exception:
                offset += max(read_size - overlap, 1)
                continue

            idx = blob.find(pattern)
            if idx != -1:
                rel_addr = offset + idx
                if self.verbose:
                    print(f"[Animator] Found coords at rel offset 0x{rel_addr:X}")
                return rel_addr

            offset += max(read_size - overlap, 1)
        return None


# ---------------- Config Loader ----------------
def load_config(path="objects.json") -> List[Dict[str, Any]]:
    with open(path, "r") as f:
        return json.load(f)["objects"]


# ---------------- Main ----------------
def main():
    animator = ICR2ObjectAnimator(verbose=True)
    animator.connect()
    try:
        config = load_config()

        threads = []
        for obj in config:
            rel_addr = animator.find_coordinates_bulk(
                tuple(obj["search_coords"]), (0, 0xF0000000)
            )
            if rel_addr is None:
                print(f"[Animator] {obj['name']} not found.")
                continue

            start_vals = animator.read_object6(rel_addr)

            if obj["mode"] == "path":
                t = threading.Thread(
                    target=animator.animate_path,
                    args=(rel_addr, start_vals, obj["waypoints"], obj["name"]),
                    daemon=True
                )
            elif obj["mode"] == "out_and_back":
                t = threading.Thread(
                    target=animator.animate_out_and_back,
                    args=(rel_addr, start_vals, obj["waypoints"], obj["name"]),
                    daemon=True
                )
            elif obj["mode"] == "spin":
                spin = tuple(obj["spin_rate_deg_per_sec"])
                t = threading.Thread(
                    target=animator.animate_spin,
                    args=(rel_addr, start_vals, spin, obj["name"]),
                    daemon=True
                )
            else:
                print(f"[Animator] Unknown mode {obj['mode']} for {obj['name']}")
                continue

            t.start()
            threads.append(t)

        # Monitor process; exit when DOSBox closes or threads die
        while True:
            if not animator.is_alive():
                print("[Main] DOSBox closed, shutting down animator.")
                break
            # If all threads have stopped, quit too
            if not any(t.is_alive() for t in threads):
                print("[Main] All animation threads exited, shutting down animator.")
                break
            time.sleep(1)


    except KeyboardInterrupt:
        print("\nAnimation interrupted by user.")
    finally:
        animator.disconnect()


if __name__ == "__main__":
    main()
