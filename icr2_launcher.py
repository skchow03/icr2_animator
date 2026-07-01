"""Tkinter launcher/editor for ICR2 object animation configs.

The launcher keeps the existing ``objects.json`` shape::

    {"objects": [ ... ]}

Object fields that are lists (``search_coords``, ``waypoints``, and
``spin_rate_deg_per_sec``) are edited as JSON snippets so current config files
can be loaded and saved without format migration.
"""

from __future__ import annotations

import json
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from animator_service import AnimatorService
from config_validation import VALID_MODES, validate_object_config
from icr2_versions import DEFAULT_ICR2_VERSION, KNOWN_ICR2_VERSIONS


DEFAULT_OBJECT: dict[str, Any] = {
    "name": "new_object",
    "search_coords": [0, 0, 0],
    "mode": "path",
    "waypoints": [
        {"x": 0, "y": 0, "z": 0, "speed_mph": 30, "rot_x": 0, "rot_y": 0, "rot_z": 0}
    ],
    "spin_rate_deg_per_sec": [0, 0, 45],
}


class ICR2Launcher(tk.Tk):
    """GUI for editing compatible object configs and controlling animations."""

    def __init__(self) -> None:
        super().__init__()
        self.title("ICR2 Animator Launcher")
        self.geometry("920x620")

        self.objects: list[dict[str, Any]] = []
        self.current_index: int | None = None
        self.service: AnimatorService | None = None
        self.worker: threading.Thread | None = None
        self.is_animating = False

        self.version_var = tk.StringVar(value=DEFAULT_ICR2_VERSION)
        self.config_path_var = tk.StringVar(value="objects.json")
        self.name_var = tk.StringVar()
        self.mode_var = tk.StringVar(value="path")
        self.status_var = tk.StringVar(value="Load or edit a config, then start animation.")

        self._build_widgets()
        self._load_config_path(Path(self.config_path_var.get()), show_errors=False)
        self._refresh_object_list()
        self._set_running_state(False)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_widgets(self) -> None:
        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(1, weight=1)

        top = ttk.Frame(root)
        top.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        top.columnconfigure(3, weight=1)

        ttk.Label(top, text="ICR2 version").grid(row=0, column=0, padx=(0, 6))
        self.version_combo = ttk.Combobox(
            top, textvariable=self.version_var, values=KNOWN_ICR2_VERSIONS, state="readonly", width=14
        )
        self.version_combo.grid(row=0, column=1, padx=(0, 14))

        ttk.Label(top, text="Config file").grid(row=0, column=2, padx=(0, 6))
        self.config_entry = ttk.Entry(top, textvariable=self.config_path_var)
        self.config_entry.grid(row=0, column=3, sticky="ew", padx=(0, 6))
        self.load_button = ttk.Button(top, text="Load", command=self._choose_and_load_config)
        self.load_button.grid(row=0, column=4, padx=3)
        self.save_button = ttk.Button(top, text="Save", command=self._save_config)
        self.save_button.grid(row=0, column=5, padx=3)
        self.save_as_button = ttk.Button(top, text="Save As...", command=self._save_config_as)
        self.save_as_button.grid(row=0, column=6, padx=3)

        left = ttk.Frame(root)
        left.grid(row=1, column=0, sticky="ns", padx=(0, 10))
        left.rowconfigure(0, weight=1)
        self.object_list = tk.Listbox(left, width=28, exportselection=False)
        self.object_list.grid(row=0, column=0, columnspan=2, sticky="ns")
        self.object_list.bind("<<ListboxSelect>>", self._on_object_select)
        self.add_button = ttk.Button(left, text="Add object", command=self._add_object)
        self.add_button.grid(row=1, column=0, sticky="ew", pady=(8, 0), padx=(0, 4))
        self.remove_button = ttk.Button(left, text="Remove object", command=self._remove_object)
        self.remove_button.grid(row=1, column=1, sticky="ew", pady=(8, 0), padx=(4, 0))

        editor = ttk.LabelFrame(root, text="Object")
        editor.grid(row=1, column=1, sticky="nsew")
        editor.columnconfigure(1, weight=1)
        editor.rowconfigure(3, weight=1)

        ttk.Label(editor, text="name").grid(row=0, column=0, sticky="nw", padx=8, pady=6)
        self.name_entry = ttk.Entry(editor, textvariable=self.name_var)
        self.name_entry.grid(row=0, column=1, sticky="ew", padx=8, pady=6)
        ttk.Label(editor, text="mode").grid(row=1, column=0, sticky="nw", padx=8, pady=6)
        self.mode_combo = ttk.Combobox(editor, textvariable=self.mode_var, values=sorted(VALID_MODES), state="readonly")
        self.mode_combo.grid(row=1, column=1, sticky="ew", padx=8, pady=6)
        ttk.Label(editor, text="search_coords (JSON list)").grid(row=2, column=0, sticky="nw", padx=8, pady=6)
        self.search_text = tk.Text(editor, height=2, wrap="none")
        self.search_text.grid(row=2, column=1, sticky="ew", padx=8, pady=6)
        ttk.Label(editor, text="waypoints (JSON list)").grid(row=3, column=0, sticky="nw", padx=8, pady=6)
        self.waypoints_text = tk.Text(editor, height=12, wrap="none")
        self.waypoints_text.grid(row=3, column=1, sticky="nsew", padx=8, pady=6)
        ttk.Label(editor, text="spin_rate_deg_per_sec (JSON list)").grid(row=4, column=0, sticky="nw", padx=8, pady=6)
        self.spin_text = tk.Text(editor, height=2, wrap="none")
        self.spin_text.grid(row=4, column=1, sticky="ew", padx=8, pady=6)
        self.apply_button = ttk.Button(editor, text="Apply object edits", command=self._apply_current_edits)
        self.apply_button.grid(row=5, column=1, sticky="e", padx=8, pady=8)

        bottom = ttk.Frame(root)
        bottom.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        bottom.columnconfigure(0, weight=1)
        ttk.Label(bottom, textvariable=self.status_var).grid(row=0, column=0, sticky="w")
        self.start_button = ttk.Button(bottom, text="Start animation", command=self._start_animation)
        self.start_button.grid(row=0, column=1, padx=4)
        self.stop_button = ttk.Button(bottom, text="Stop animation", command=self._stop_animation)
        self.stop_button.grid(row=0, column=2, padx=4)

    def _choose_and_load_config(self) -> None:
        if not self._ensure_stopped_for_edits():
            return
        path = filedialog.askopenfilename(title="Load objects.json", filetypes=(("JSON", "*.json"), ("All", "*.*")))
        if path:
            self._load_config_path(Path(path), show_errors=True)

    def _load_config_path(self, path: Path, show_errors: bool) -> None:
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            self.objects = data.get("objects", [])
            if not isinstance(self.objects, list):
                raise ValueError("config 'objects' must be a list")
            self.config_path_var.set(str(path))
            self.current_index = 0 if self.objects else None
            self.status_var.set(f"Loaded {len(self.objects)} object(s) from {path}.")
        except Exception as exc:
            if show_errors:
                messagebox.showerror("Load failed", str(exc))
            if not self.objects:
                self.objects = []
                self.current_index = None
        self._refresh_object_list()
        self._populate_editor()

    def _save_config(self) -> None:
        if not self._ensure_stopped_for_edits() or not self._apply_current_edits():
            return
        self._write_config(Path(self.config_path_var.get()))

    def _save_config_as(self) -> None:
        if not self._ensure_stopped_for_edits() or not self._apply_current_edits():
            return
        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=(("JSON", "*.json"), ("All", "*.*")))
        if path:
            self.config_path_var.set(path)
            self._write_config(Path(path))

    def _write_config(self, path: Path) -> None:
        path.write_text(json.dumps({"objects": self.objects}, indent=2) + "\n", encoding="utf-8")
        self.status_var.set(f"Saved {len(self.objects)} object(s) to {path}.")

    def _refresh_object_list(self) -> None:
        self.object_list.delete(0, tk.END)
        for index, obj in enumerate(self.objects):
            self.object_list.insert(tk.END, obj.get("name") or f"object #{index + 1}")
        if self.current_index is not None and self.current_index < len(self.objects):
            self.object_list.selection_set(self.current_index)

    def _on_object_select(self, _event: tk.Event) -> None:
        if self.is_animating:
            return
        selection = self.object_list.curselection()
        if selection:
            self.current_index = selection[0]
            self._populate_editor()

    def _populate_editor(self) -> None:
        obj = self.objects[self.current_index] if self.current_index is not None and self.objects else DEFAULT_OBJECT
        self.name_var.set(obj.get("name", ""))
        self.mode_var.set(obj.get("mode", "path"))
        self._set_text(self.search_text, json.dumps(obj.get("search_coords", [])))
        self._set_text(self.waypoints_text, json.dumps(obj.get("waypoints", []), indent=2))
        self._set_text(self.spin_text, json.dumps(obj.get("spin_rate_deg_per_sec", [0, 0, 0])))

    def _set_text(self, widget: tk.Text, value: str) -> None:
        widget.delete("1.0", tk.END)
        widget.insert("1.0", value)

    def _apply_current_edits(self) -> bool:
        if self.is_animating:
            messagebox.showinfo("Animation running", "Stop animation before applying edits.")
            return False
        if self.current_index is None:
            return True
        try:
            updated = {
                "name": self.name_var.get(),
                "search_coords": json.loads(self.search_text.get("1.0", "end-1c")),
                "mode": self.mode_var.get(),
                "waypoints": json.loads(self.waypoints_text.get("1.0", "end-1c")),
                "spin_rate_deg_per_sec": json.loads(self.spin_text.get("1.0", "end-1c")),
            }
        except json.JSONDecodeError as exc:
            messagebox.showerror("Invalid JSON", str(exc))
            return False
        self.objects[self.current_index] = updated
        self._refresh_object_list()
        return True

    def _add_object(self) -> None:
        if not self._ensure_stopped_for_edits() or not self._apply_current_edits():
            return
        self.objects.append(json.loads(json.dumps(DEFAULT_OBJECT)))
        self.current_index = len(self.objects) - 1
        self._refresh_object_list()
        self._populate_editor()

    def _remove_object(self) -> None:
        if not self._ensure_stopped_for_edits() or self.current_index is None:
            return
        del self.objects[self.current_index]
        self.current_index = min(self.current_index, len(self.objects) - 1) if self.objects else None
        self._refresh_object_list()
        self._populate_editor()

    def _start_animation(self) -> None:
        if not self._apply_current_edits():
            return
        errors = validate_object_config(self.objects)
        if errors:
            messagebox.showerror("Config validation error", "\n".join(f"• {error}" for error in errors))
            return
        self.service = AnimatorService(version=self.version_var.get(), verbose=True)
        self.status_var.set("Starting animation...")
        self._set_running_state(True)
        self.worker = threading.Thread(target=self._run_service, args=(list(self.objects),), daemon=True)
        self.worker.start()

    def _run_service(self, objects: list[dict[str, Any]]) -> None:
        try:
            if self.service:
                self.service.start(objects)
                self.service.wait()
        except Exception as exc:
            self.after(0, lambda: messagebox.showerror("Animator error", str(exc)))
        finally:
            self.after(0, lambda: self._set_running_state(False))

    def _stop_animation(self) -> None:
        self.status_var.set("Stopping animation...")
        if self.service:
            self.service.stop()
        self._set_running_state(False)

    def _set_running_state(self, running: bool) -> None:
        self.is_animating = running
        edit_state = "disabled" if running else "normal"
        readonly_state = "disabled" if running else "readonly"
        for widget in (self.config_entry, self.name_entry, self.search_text, self.waypoints_text, self.spin_text,
                       self.load_button, self.save_button, self.save_as_button, self.add_button,
                       self.remove_button, self.apply_button):
            widget.configure(state=edit_state)
        self.version_combo.configure(state=readonly_state)
        self.mode_combo.configure(state=readonly_state)
        self.object_list.configure(state=edit_state)
        self.start_button.configure(state="disabled" if running else "normal")
        self.stop_button.configure(state="normal" if running else "disabled")
        if not running:
            self.status_var.set("Stopped. Load or edit a config, then start animation.")

    def _ensure_stopped_for_edits(self) -> bool:
        if self.is_animating:
            messagebox.showinfo("Animation running", "Stop animation before editing the config.")
            return False
        return True

    def _on_close(self) -> None:
        if self.service:
            self.service.stop()
        self.destroy()


def main() -> None:
    ICR2Launcher().mainloop()


if __name__ == "__main__":
    main()
