"""Tkinter launcher for configuring and starting ICR2 object animations."""

from __future__ import annotations

import copy
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any

from animator_service import AnimatorService
from config_validation import validate_object_config
from icr2_versions import DEFAULT_ICR2_VERSION, KNOWN_ICR2_VERSIONS

WAYPOINT_COLUMNS = ("x", "y", "z", "speed_mph", "rot_x", "rot_y", "rot_z")
DEFAULT_WAYPOINT = {"x": 0, "y": 0, "z": 0, "speed_mph": 60, "rot_x": 0, "rot_y": 0, "rot_z": 0}


class AnimatorLauncher(tk.Tk):
    """Small UI for selecting an ICR2 version/config and controlling animation."""

    def __init__(self):
        super().__init__()
        self.title("ICR2 Object Animator")
        self.resizable(False, False)

        self.service: AnimatorService | None = None
        self.worker: threading.Thread | None = None
        self.objects: list[dict[str, Any]] = []
        self.selected_object_index: int | None = None
        self.version_var = tk.StringVar(value=DEFAULT_ICR2_VERSION)
        self.config_var = tk.StringVar(value="objects.json")
        self.object_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Select a version and config, then start.")

        self._build_widgets()
        self._load_config_for_editing(show_errors=False)
        self._update_start_state()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_widgets(self):
        padding = {"padx": 8, "pady": 6}
        frame = ttk.Frame(self, padding=12)
        frame.grid(row=0, column=0, sticky="nsew")

        ttk.Label(frame, text="ICR2 version").grid(row=0, column=0, sticky="w", **padding)
        self.version_combo = ttk.Combobox(
            frame,
            textvariable=self.version_var,
            values=KNOWN_ICR2_VERSIONS,
            state="readonly",
            width=18,
        )
        self.version_combo.grid(row=0, column=1, sticky="ew", **padding)
        self.version_combo.bind("<<ComboboxSelected>>", lambda _event: self._update_start_state())

        ttk.Label(frame, text="Config").grid(row=1, column=0, sticky="w", **padding)
        self.config_entry = ttk.Entry(frame, textvariable=self.config_var, width=36)
        self.config_entry.grid(row=1, column=1, sticky="ew", **padding)
        self.config_entry.bind("<KeyRelease>", self._config_path_changed)
        ttk.Button(frame, text="Browse...", command=self._browse_config).grid(row=1, column=2, **padding)
        ttk.Button(frame, text="Load", command=self._load_config_for_editing).grid(row=1, column=3, **padding)

        ttk.Label(frame, text="Object").grid(row=2, column=0, sticky="w", **padding)
        self.object_combo = ttk.Combobox(frame, textvariable=self.object_var, state="readonly", width=36)
        self.object_combo.grid(row=2, column=1, columnspan=2, sticky="ew", **padding)
        self.object_combo.bind("<<ComboboxSelected>>", self._select_object)

        table_frame = ttk.Frame(frame)
        table_frame.grid(row=3, column=0, columnspan=4, sticky="nsew", **padding)
        self.waypoint_table = ttk.Treeview(
            table_frame,
            columns=WAYPOINT_COLUMNS,
            show="headings",
            height=7,
            selectmode="browse",
        )
        for column in WAYPOINT_COLUMNS:
            self.waypoint_table.heading(column, text=column)
            self.waypoint_table.column(column, width=90, anchor="e")
        self.waypoint_table.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.waypoint_table.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.waypoint_table.configure(yscrollcommand=scrollbar.set)
        self.waypoint_table.bind("<Double-1>", self._edit_waypoint_cell)

        button_frame = ttk.Frame(frame)
        button_frame.grid(row=4, column=0, columnspan=4, sticky="ew", **padding)
        ttk.Button(button_frame, text="Add waypoint", command=self._add_waypoint).grid(row=0, column=0, padx=4)
        ttk.Button(button_frame, text="Remove waypoint", command=self._remove_waypoint).grid(row=0, column=1, padx=4)
        ttk.Button(button_frame, text="Move up", command=lambda: self._move_waypoint(-1)).grid(row=0, column=2, padx=4)
        ttk.Button(button_frame, text="Move down", command=lambda: self._move_waypoint(1)).grid(row=0, column=3, padx=4)

        self.start_button = ttk.Button(frame, text="Start", command=self._start)
        self.start_button.grid(row=5, column=2, sticky="e", **padding)
        self.stop_button = ttk.Button(frame, text="Stop", command=self._stop, state="disabled")
        self.stop_button.grid(row=5, column=3, sticky="e", **padding)

        ttk.Label(frame, textvariable=self.status_var).grid(
            row=6, column=0, columnspan=4, sticky="w", **padding
        )

    def _config_path_changed(self, _event=None):
        self.objects = []
        self.selected_object_index = None
        self.object_combo.configure(values=[])
        self.object_var.set("")
        self._refresh_waypoint_table()
        self.status_var.set("Click Load to read the selected config.")
        self._update_start_state()

    def _browse_config(self):
        path = filedialog.askopenfilename(
            title="Select object config",
            filetypes=(("JSON files", "*.json"), ("All files", "*.*")),
        )
        if path:
            self.config_var.set(path)
            self._load_config_for_editing()

    def _load_config_for_editing(self, show_errors: bool = True):
        try:
            objects = AnimatorService(version=self.version_var.get(), verbose=False).load_objects(self.config_var.get())
        except Exception as exc:
            self.objects = []
            self.selected_object_index = None
            self.object_combo.configure(values=[])
            self.object_var.set("")
            self._refresh_waypoint_table()
            if show_errors:
                messagebox.showerror("Config error", str(exc))
            self._update_start_state()
            return

        self.objects = copy.deepcopy(objects)
        object_labels = [self._object_label(index, obj) for index, obj in enumerate(self.objects)]
        self.object_combo.configure(values=object_labels)
        if self.objects:
            self.selected_object_index = 0
            self.object_var.set(object_labels[0])
        else:
            self.selected_object_index = None
            self.object_var.set("")
        self._refresh_waypoint_table()
        self.status_var.set("Config loaded. Double-click a waypoint cell to edit it.")
        self._update_start_state()

    def _object_label(self, index: int, obj: dict[str, Any]) -> str:
        return f"{index + 1}. {obj.get('name', 'unnamed')} ({obj.get('mode', 'unknown')})"

    def _select_object(self, _event=None):
        selection = self.object_combo.current()
        self.selected_object_index = selection if selection >= 0 else None
        self._refresh_waypoint_table()

    def _selected_object(self) -> dict[str, Any] | None:
        if self.selected_object_index is None:
            return None
        if self.selected_object_index >= len(self.objects):
            return None
        return self.objects[self.selected_object_index]

    def _selected_waypoint_index(self) -> int | None:
        selected = self.waypoint_table.selection()
        if not selected:
            return None
        return self.waypoint_table.index(selected[0])

    def _refresh_waypoint_table(self, selected_index: int | None = None):
        for item in self.waypoint_table.get_children():
            self.waypoint_table.delete(item)

        obj = self._selected_object()
        if not obj or obj.get("mode") not in {"path", "out_and_back"}:
            return

        for waypoint in obj.get("waypoints", []):
            values = [waypoint.get(column, "") for column in WAYPOINT_COLUMNS]
            self.waypoint_table.insert("", "end", values=values)

        children = self.waypoint_table.get_children()
        if selected_index is not None and children:
            selected_index = max(0, min(selected_index, len(children) - 1))
            self.waypoint_table.selection_set(children[selected_index])
            self.waypoint_table.focus(children[selected_index])

    def _edit_waypoint_cell(self, event):
        item = self.waypoint_table.identify_row(event.y)
        column_id = self.waypoint_table.identify_column(event.x)
        if not item or not column_id:
            return

        column_index = int(column_id.removeprefix("#")) - 1
        if column_index < 0 or column_index >= len(WAYPOINT_COLUMNS):
            return

        bbox = self.waypoint_table.bbox(item, column_id)
        if not bbox:
            return

        x, y, width, height = bbox
        column = WAYPOINT_COLUMNS[column_index]
        current_value = self.waypoint_table.set(item, column)
        editor = ttk.Entry(self.waypoint_table)
        editor.insert(0, current_value)
        editor.select_range(0, tk.END)
        editor.focus_set()
        editor.place(x=x, y=y, width=width, height=height)

        committed = False

        def commit(_event=None):
            nonlocal committed
            if committed:
                return
            committed = True
            self._set_waypoint_value(self.waypoint_table.index(item), column, editor.get())
            editor.destroy()

        def cancel(_event=None):
            nonlocal committed
            committed = True
            editor.destroy()

        editor.bind("<Return>", commit)
        editor.bind("<FocusOut>", commit)
        editor.bind("<Escape>", cancel)

    def _set_waypoint_value(self, index: int, column: str, value: str):
        obj = self._selected_object()
        if not obj:
            return
        waypoints = obj.setdefault("waypoints", [])
        if index >= len(waypoints):
            return

        stripped = value.strip()
        if stripped == "" and column not in {"x", "y", "z"}:
            waypoints[index].pop(column, None)
        else:
            try:
                number = float(stripped)
            except ValueError:
                messagebox.showerror("Invalid value", f"{column} must be numeric.")
                return
            waypoints[index][column] = int(number) if number.is_integer() else number
        self._refresh_waypoint_table(index)

    def _add_waypoint(self):
        obj = self._selected_object()
        if not obj:
            return
        if obj.get("mode") not in {"path", "out_and_back"}:
            messagebox.showinfo("Waypoints unavailable", "Only path and out_and_back objects use waypoints.")
            return
        waypoints = obj.setdefault("waypoints", [])
        insert_at = self._selected_waypoint_index()
        new_waypoint = copy.deepcopy(waypoints[insert_at] if insert_at is not None and waypoints else DEFAULT_WAYPOINT)
        if insert_at is None:
            waypoints.append(new_waypoint)
            insert_at = len(waypoints) - 1
        else:
            insert_at += 1
            waypoints.insert(insert_at, new_waypoint)
        self._refresh_waypoint_table(insert_at)

    def _remove_waypoint(self):
        obj = self._selected_object()
        index = self._selected_waypoint_index()
        if not obj or index is None:
            return
        waypoints = obj.get("waypoints", [])
        if index < len(waypoints):
            del waypoints[index]
        self._refresh_waypoint_table(index)

    def _move_waypoint(self, direction: int):
        obj = self._selected_object()
        index = self._selected_waypoint_index()
        if not obj or index is None:
            return
        waypoints = obj.get("waypoints", [])
        new_index = index + direction
        if not 0 <= new_index < len(waypoints):
            return
        waypoints[index], waypoints[new_index] = waypoints[new_index], waypoints[index]
        self._refresh_waypoint_table(new_index)

    def _update_start_state(self):
        can_start = bool(
            self.version_var.get() in KNOWN_ICR2_VERSIONS and self.config_var.get() and self.objects
        )
        if self.service and self.service.is_running():
            can_start = False
        self.start_button.configure(state="normal" if can_start else "disabled")

    def _start(self):
        selected_version = self.version_var.get()
        if not self.objects:
            self._load_config_for_editing()
        objects = copy.deepcopy(self.objects)
        self.service = AnimatorService(version=selected_version, verbose=True)

        validation_errors = validate_object_config(objects)
        if validation_errors:
            error_text = "\n".join(f"• {error}" for error in validation_errors)
            self.status_var.set("Config validation failed; animations were not started.")
            messagebox.showerror("Config validation error", error_text)
            self.service = None
            self._update_start_state()
            return

        self.status_var.set(f"Starting animations for {selected_version}...")
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.worker = threading.Thread(target=self._run_service, args=(objects,), daemon=True)
        self.worker.start()

    def _run_service(self, objects):
        try:
            if not self.service:
                return
            self.service.start(objects)
            self.service.wait()
        except Exception as exc:
            self.after(0, lambda: messagebox.showerror("Animator error", str(exc)))
        finally:
            self.after(0, self._mark_stopped)

    def _stop(self):
        if self.service:
            self.status_var.set("Stopping animations...")
            self.service.stop()
        self._mark_stopped()

    def _mark_stopped(self):
        self.stop_button.configure(state="disabled")
        self.status_var.set("Stopped. Select a version and config, then start.")
        self._update_start_state()

    def _on_close(self):
        if self.service:
            self.service.stop()
        self.destroy()


def main():
    AnimatorLauncher().mainloop()


if __name__ == "__main__":
    main()
