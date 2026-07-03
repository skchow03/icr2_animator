"""Tkinter launcher/editor for ICR2 object animation configs.

The launcher keeps the existing ``objects.json`` shape::

    {"objects": [ ... ]}

Run this primary launcher with::

    python icr2_launcher.py

Object fields that are lists (``search_coords``, ``waypoints``, and
``spin_rate_deg_per_sec``) can still be edited as JSON snippets so current
config files can be loaded and saved without format migration. Waypoints also
have a table editor for common add/remove/reorder/cell-edit operations. Each
object can also define ``start_delay_seconds`` to delay its animation after the
user clicks Start animation, and ``start_position`` to immediately teleport from
the found memory/default position before animating.
"""

from __future__ import annotations

import copy
import json
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, TextIO

from animator_service import AnimatorService
from app_settings import (
    get_window_keywords,
    load_app_settings,
    parse_window_keywords,
    save_app_settings,
)
from icr2_logging import log_error, log_info
from config_validation import VALID_MODES, validate_object_config
from icr2_versions import KNOWN_ICR2_VERSIONS

WAYPOINT_COLUMNS = ("x", "y", "z", "speed_mph", "rot_x", "rot_y", "rot_z")
DEFAULT_WAYPOINT = {
    "x": 0,
    "y": 0,
    "z": 0,
    "speed_mph": 60,
    "rot_x": 0,
    "rot_y": 0,
    "rot_z": 0,
}


DEFAULT_OBJECT: dict[str, Any] = {
    "name": "new_object",
    "search_coords": [0, 0, 0],
    "mode": "ping_pong_path",
    "start_delay_seconds": 0,
    "waypoints": [
        {"x": 0, "y": 0, "z": 0, "speed_mph": 30, "rot_x": 0, "rot_y": 0, "rot_z": 0}
    ],
    "spin_rate_deg_per_sec": [0, 0, 45],
}


TOOLTIPS = {
    "version": "Which ICR2/DOSBox build to attach to. This must match the running DOSBox window/version so memory offsets are interpreted correctly.",
    "window_keywords": "Comma-separated words that must appear in the DOSBox window title for the selected ICR2 version. Saved per version in the application INI file.",
    "config": "JSON file containing the object definitions. Load reads it into this editor; Save writes the current objects back to disk.",
    "fps": "Animation update rate. Higher values are smoother but use more CPU. This does not change game FPS.",
    "object_list": "Objects in the current config. Select one to edit its animation settings.",
    "name": "Friendly name used in the object list, console messages, and validation errors.",
    "mode": "How this object repeats. See the mode explanation below for the selected mode.",
    "start_delay": "Seconds to wait after Start animation before this object begins moving. Use this to stagger multiple objects.",
    "use_start_position": "When enabled, Start animation instantly teleports the object from its found/default memory location to these coordinates before movement begins. Stop restores the original found location.",
    "start_position": "Optional animation start coordinates in integer 1/500-inch units. Loops use this as their start instead of the found memory location.",
    "search_coords": "The object's current in-game x/y/z coordinates used to find its memory record when animation starts. Coordinates are integer 1/500-inch units.",
    "waypoints": "Path targets for movement modes. x/y/z are 1/500-inch units; speed_mph controls travel to that row; rot_x/rot_y/rot_z are target angles in degrees.",
    "waypoint_buttons": "Add duplicates the selected waypoint (or a default point), Remove deletes the selected row, and Move changes playback order.",
    "spin_rate": "Rotation speeds for rotate_in_place only, in degrees per second around pitch, yaw, and roll axes. Positive/negative values spin opposite directions.",
    "tooltips_toggle": "Turn hover help popups on or off. The setting only affects tooltips, not the always-visible help text.",
    "start": "Validates the config, connects to DOSBox, searches for each object, teleports any configured start_position, and starts its animation thread.",
    "stop": "Stops all animations and restores discovered objects to the positions/rotations captured before any start_position teleport.",
}

MODE_DESCRIPTIONS = {
    "ping_pong_path": "Move start → each waypoint → back through the same waypoints in reverse, then repeat. Best for shuttles/patrols that should retrace their path smoothly.",
    "return_to_start": "Move start → each waypoint → directly back to the original start point, then repeat. Best when the return leg should be a single straight segment.",
    "reset_loop": "Move start → each waypoint, then instantly snap back to the original start point and repeat. Best for one-way looping effects where the reset can be hidden.",
    "rotate_in_place": "Keep the original x/y/z position and continuously rotate using the spin-rate fields. Waypoints are ignored in this mode.",
}

GENERAL_HELP = (
    "Coordinates are integer 1/500-inch game units. Rotation waypoint fields are degrees, while the animator converts them to ICR2 memory units. "
    "For movement modes, each waypoint row is a destination and its speed_mph is the speed used while travelling to that destination. "
    "For rotate_in_place, use spin rate instead of waypoints. Enable Start position to teleport an object from its found/default memory location before animating; loop resets use that start position, while Stop restores found objects to their original memory values."
)


class ToolTip:
    """Small hover tooltip for Tkinter/ttk widgets."""

    def __init__(
        self,
        widget: tk.Widget,
        text: str,
        *,
        wraplength: int = 360,
        enabled_callback: Any | None = None,
    ) -> None:
        self.widget = widget
        self.text = text
        self.wraplength = wraplength
        self.enabled_callback = enabled_callback
        self.tip_window: tk.Toplevel | None = None
        widget.bind("<Enter>", self.show, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")

    def show(self, _event: tk.Event | None = None) -> None:
        if (
            self.tip_window
            or not self.text
            or (self.enabled_callback and not self.enabled_callback())
        ):
            return
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self.tip_window = tk.Toplevel(self.widget)
        self.tip_window.wm_overrideredirect(True)
        self.tip_window.wm_geometry(f"+{x}+{y}")
        label = ttk.Label(
            self.tip_window,
            text=self.text,
            justify="left",
            wraplength=self.wraplength,
            relief="solid",
            borderwidth=1,
            padding=(8, 5),
        )
        label.pack()

    def hide(self, _event: tk.Event | None = None) -> None:
        if self.tip_window:
            self.tip_window.destroy()
            self.tip_window = None


class ConsoleRedirector:
    """File-like stream that mirrors console output into the launcher log."""

    def __init__(self, original: TextIO, write_callback) -> None:
        self.original = original
        self.write_callback = write_callback

    def write(self, message: str) -> int:
        self.original.write(message)
        self.original.flush()
        if message:
            self.write_callback(message)
        return len(message)

    def flush(self) -> None:
        self.original.flush()


class ICR2Launcher(tk.Tk):
    """GUI for editing compatible object configs and controlling animations."""

    def __init__(self) -> None:
        super().__init__()
        self.title("ICR2 Animator Launcher")
        self.geometry("1260x700")

        self.objects: list[dict[str, Any]] = []
        self.current_index: int | None = None
        self.service: AnimatorService | None = None
        self.worker: threading.Thread | None = None
        self.is_animating = False
        self.is_dirty = False
        self._populating_editor = False
        self._original_stdout = sys.stdout
        self._original_stderr = sys.stderr

        self.app_settings = load_app_settings()
        selected_version = self.app_settings.selected_version()
        config_path = self.app_settings.config_path()
        fps = self.app_settings.fps()
        tooltips_enabled = self.app_settings.tooltips_enabled()

        self.version_var = tk.StringVar(value=selected_version)
        self.window_keywords_var = tk.StringVar()
        self.config_path_var = tk.StringVar(value=config_path)
        self.fps_var = tk.StringVar(value=fps)
        self.name_var = tk.StringVar()
        self.mode_var = tk.StringVar(value="ping_pong_path")
        self.start_delay_var = tk.StringVar(value="0")
        self.use_start_position_var = tk.BooleanVar(value=False)
        self.start_position_vars = [tk.StringVar(value="0") for _ in range(3)]
        self.search_coord_vars = [tk.StringVar(value="0") for _ in range(3)]
        self.spin_rate_vars = [tk.StringVar(value="0") for _ in range(3)]
        self.status_var = tk.StringVar(
            value="Load or edit a config, then start animation."
        )
        self.dirty_status_var = tk.StringVar(value="Saved")
        self.tooltips_enabled_var = tk.BooleanVar(value=tooltips_enabled)
        self.tooltips: list[ToolTip] = []

        self._load_window_keywords_for_selected_version()
        self._install_settings_traces()
        self._build_widgets()
        self._install_console_redirectors()
        self._load_config_path(Path(self.config_path_var.get()), show_errors=False)
        self._refresh_object_list()
        self._set_running_state(False)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_widgets(self) -> None:
        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)
        root.columnconfigure(1, weight=1)
        root.columnconfigure(2, weight=1)
        root.rowconfigure(1, weight=1)

        top = ttk.Frame(root)
        top.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 8))
        top.columnconfigure(5, weight=1)

        version_label = ttk.Label(top, text="ICR2 version")
        version_label.grid(row=0, column=0, padx=(0, 6))
        self.version_combo = ttk.Combobox(
            top,
            textvariable=self.version_var,
            values=KNOWN_ICR2_VERSIONS,
            state="readonly",
            width=14,
        )
        self.version_combo.grid(row=0, column=1, padx=(0, 10))
        self.version_combo.bind("<<ComboboxSelected>>", self._on_version_selected)
        keywords_label = ttk.Label(top, text="Window keywords")
        keywords_label.grid(row=0, column=2, padx=(0, 6))
        self.window_keywords_entry = ttk.Entry(
            top, textvariable=self.window_keywords_var, width=24
        )
        self.window_keywords_entry.grid(row=0, column=3, sticky="ew", padx=(0, 14))

        config_label = ttk.Label(top, text="Config file")
        config_label.grid(row=0, column=4, padx=(0, 6))
        self.config_entry = ttk.Entry(top, textvariable=self.config_path_var)
        self.config_entry.grid(row=0, column=5, sticky="ew", padx=(0, 6))
        self.load_button = ttk.Button(
            top, text="Load", command=self._choose_and_load_config
        )
        self.load_button.grid(row=0, column=6, padx=3)
        self.save_button = ttk.Button(top, text="Save", command=self._save_config)
        self.save_button.grid(row=0, column=7, padx=3)
        self.save_as_button = ttk.Button(
            top, text="Save As...", command=self._save_config_as
        )
        self.save_as_button.grid(row=0, column=8, padx=3)
        fps_label = ttk.Label(top, text="FPS")
        fps_label.grid(row=0, column=9, padx=(14, 6))
        self.fps_entry = ttk.Entry(top, textvariable=self.fps_var, width=8)
        self.fps_entry.grid(row=0, column=10, padx=3)
        self.tooltips_check = ttk.Checkbutton(
            top,
            text="Tooltips",
            variable=self.tooltips_enabled_var,
            command=self._on_tooltips_toggle,
        )
        self.tooltips_check.grid(row=0, column=11, padx=(10, 0))

        left = ttk.Frame(root)
        left.grid(row=1, column=0, sticky="ns", padx=(0, 10))
        left.rowconfigure(0, weight=1)
        self.object_list = tk.Listbox(left, width=28, exportselection=False)
        self.object_list.grid(row=0, column=0, columnspan=2, sticky="ns")
        self.object_list.bind("<<ListboxSelect>>", self._on_object_select)
        self.add_button = ttk.Button(left, text="Add object", command=self._add_object)
        self.add_button.grid(row=1, column=0, sticky="ew", pady=(8, 0), padx=(0, 4))
        self.remove_button = ttk.Button(
            left, text="Remove object", command=self._remove_object
        )
        self.remove_button.grid(row=1, column=1, sticky="ew", pady=(8, 0), padx=(4, 0))

        editor = ttk.LabelFrame(root, text="Object")
        editor.grid(row=1, column=1, sticky="nsew")
        editor.columnconfigure(1, weight=1)
        editor.rowconfigure(6, weight=1)

        name_label = ttk.Label(editor, text="Object name")
        name_label.grid(row=0, column=0, sticky="nw", padx=8, pady=6)
        self.name_entry = ttk.Entry(editor, textvariable=self.name_var)
        self.name_entry.grid(row=0, column=1, sticky="ew", padx=8, pady=6)
        mode_label = ttk.Label(editor, text="Animation mode")
        mode_label.grid(row=1, column=0, sticky="nw", padx=8, pady=6)
        self.mode_combo = ttk.Combobox(
            editor,
            textvariable=self.mode_var,
            values=sorted(VALID_MODES),
            state="readonly",
        )
        self.mode_combo.grid(row=1, column=1, sticky="ew", padx=8, pady=6)
        self.mode_combo.bind("<<ComboboxSelected>>", self._on_mode_selected)
        self.mode_help_label = ttk.Label(
            editor, text="", wraplength=640, foreground="#444"
        )
        self.mode_help_label.grid(row=2, column=1, sticky="ew", padx=8, pady=(0, 6))
        delay_label = ttk.Label(editor, text="Start delay (seconds)")
        delay_label.grid(row=3, column=0, sticky="nw", padx=8, pady=6)
        self.start_delay_entry = ttk.Entry(editor, textvariable=self.start_delay_var)
        self.start_delay_entry.grid(row=3, column=1, sticky="ew", padx=8, pady=6)
        self.start_position_check = ttk.Checkbutton(
            editor,
            text="Teleport to start position",
            variable=self.use_start_position_var,
            command=self._auto_apply_current_edits,
        )
        self.start_position_check.grid(row=4, column=0, sticky="nw", padx=8, pady=6)
        start_position_frame = ttk.Frame(editor)
        start_position_frame.grid(row=4, column=1, sticky="ew", padx=8, pady=6)
        self.start_position_entries = self._build_vector_inputs(
            start_position_frame, self.start_position_vars, ("x", "y", "z")
        )

        search_label = ttk.Label(editor, text="Find object at")
        search_label.grid(row=5, column=0, sticky="nw", padx=8, pady=6)
        search_frame = ttk.Frame(editor)
        search_frame.grid(row=5, column=1, sticky="ew", padx=8, pady=6)
        self.search_entries = self._build_vector_inputs(
            search_frame, self.search_coord_vars, ("x", "y", "z")
        )

        waypoints_label = ttk.Label(editor, text="Waypoints")
        waypoints_label.grid(row=6, column=0, sticky="nw", padx=8, pady=6)
        waypoint_area = ttk.Frame(editor)
        waypoint_area.grid(row=6, column=1, sticky="nsew", padx=8, pady=6)
        waypoint_area.columnconfigure(0, weight=1)
        waypoint_area.rowconfigure(1, weight=1)
        ttk.Label(
            waypoint_area,
            text="Double-click a cell to edit. Use the buttons to add, duplicate, remove, or reorder points.",
        ).grid(row=0, column=0, sticky="w", pady=(0, 4))
        self.waypoints_text = tk.Text(editor, height=8, wrap="none")
        self.waypoints_text.bind(
            "<FocusOut>", lambda _event: self._refresh_waypoint_table()
        )
        waypoint_tools = ttk.Frame(editor)
        waypoint_tools.grid(row=7, column=1, sticky="ew", padx=8, pady=(0, 6))
        self.add_waypoint_button = ttk.Button(
            waypoint_tools, text="Add waypoint", command=self._add_waypoint
        )
        self.add_waypoint_button.grid(row=0, column=0, padx=(0, 4))
        self.remove_waypoint_button = ttk.Button(
            waypoint_tools, text="Remove waypoint", command=self._remove_waypoint
        )
        self.remove_waypoint_button.grid(row=0, column=1, padx=4)
        self.move_waypoint_up_button = ttk.Button(
            waypoint_tools, text="Move up", command=lambda: self._move_waypoint(-1)
        )
        self.move_waypoint_up_button.grid(row=0, column=2, padx=4)
        self.move_waypoint_down_button = ttk.Button(
            waypoint_tools, text="Move down", command=lambda: self._move_waypoint(1)
        )
        self.move_waypoint_down_button.grid(row=0, column=3, padx=4)

        self.waypoint_table = ttk.Treeview(
            waypoint_area,
            columns=WAYPOINT_COLUMNS,
            show="headings",
            height=6,
            selectmode="browse",
        )
        for column in WAYPOINT_COLUMNS:
            self.waypoint_table.heading(column, text=column)
            self.waypoint_table.column(column, width=80, anchor="e")
        self.waypoint_table.grid(row=1, column=0, sticky="nsew")
        self.waypoint_table.bind("<Double-1>", self._edit_waypoint_cell)

        spin_label = ttk.Label(editor, text="Spin rate (deg/sec)")
        spin_label.grid(row=8, column=0, sticky="nw", padx=8, pady=6)
        spin_frame = ttk.Frame(editor)
        spin_frame.grid(row=8, column=1, sticky="ew", padx=8, pady=6)
        self.spin_entries = self._build_vector_inputs(
            spin_frame, self.spin_rate_vars, ("pitch", "yaw", "roll")
        )

        self.search_text = tk.Text(editor, height=2, wrap="none")
        self.spin_text = tk.Text(editor, height=2, wrap="none")
        self.general_help_label = ttk.Label(
            editor, text=GENERAL_HELP, wraplength=760, foreground="#444"
        )
        self.general_help_label.grid(
            row=9, column=0, columnspan=2, sticky="ew", padx=8, pady=(4, 0)
        )
        for widget in (self.name_entry, self.start_delay_entry, self.waypoints_text):
            self._bind_auto_apply(widget)
        console = ttk.LabelFrame(root, text="Console messages")
        console.grid(row=1, column=2, sticky="nsew", padx=(10, 0))
        console.rowconfigure(0, weight=1)
        console.columnconfigure(0, weight=1)
        self.console_text = tk.Text(
            console, height=10, width=36, wrap="word", state="disabled"
        )
        self.console_text.grid(row=0, column=0, sticky="nsew")
        self.console_scrollbar = ttk.Scrollbar(
            console, orient="vertical", command=self.console_text.yview
        )
        self.console_scrollbar.grid(row=0, column=1, sticky="ns")
        self.console_text.configure(yscrollcommand=self.console_scrollbar.set)

        bottom = ttk.Frame(root)
        bottom.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        bottom.columnconfigure(0, weight=1)
        ttk.Label(bottom, textvariable=self.status_var).grid(
            row=0, column=0, sticky="w"
        )
        self.dirty_status_label = ttk.Label(
            bottom, textvariable=self.dirty_status_var, foreground="#b35c00"
        )
        self.dirty_status_label.grid(row=0, column=1, sticky="e", padx=(8, 12))
        self.start_button = ttk.Button(
            bottom, text="Start animation", command=self._start_animation
        )
        self.start_button.grid(row=0, column=2, padx=4)
        self.stop_button = ttk.Button(
            bottom, text="Stop animation", command=self._stop_animation
        )
        self.stop_button.grid(row=0, column=3, padx=4)

        self._add_tooltips(
            {
                version_label: TOOLTIPS["version"],
                self.version_combo: TOOLTIPS["version"],
                keywords_label: TOOLTIPS["window_keywords"],
                self.window_keywords_entry: TOOLTIPS["window_keywords"],
                config_label: TOOLTIPS["config"],
                self.config_entry: TOOLTIPS["config"],
                self.load_button: "Choose a config JSON file to load into the editor.",
                self.save_button: "Apply edits and save to the current config file.",
                self.save_as_button: "Apply edits and save to a new JSON file.",
                fps_label: TOOLTIPS["fps"],
                self.fps_entry: TOOLTIPS["fps"],
                self.tooltips_check: TOOLTIPS["tooltips_toggle"],
                self.object_list: TOOLTIPS["object_list"],
                self.console_text: "Live log of messages printed to the console by the launcher and animator service.",
                name_label: TOOLTIPS["name"],
                self.name_entry: TOOLTIPS["name"],
                mode_label: TOOLTIPS["mode"],
                self.mode_combo: TOOLTIPS["mode"],
                self.mode_help_label: "This explanation changes when you choose a different animation mode.",
                delay_label: TOOLTIPS["start_delay"],
                self.start_delay_entry: TOOLTIPS["start_delay"],
                self.start_position_check: TOOLTIPS["use_start_position"],
                start_position_frame: TOOLTIPS["start_position"],
                search_label: TOOLTIPS["search_coords"],
                search_frame: TOOLTIPS["search_coords"],
                waypoints_label: TOOLTIPS["waypoints"],
                self.waypoint_table: TOOLTIPS["waypoints"],
                waypoint_tools: TOOLTIPS["waypoint_buttons"],
                spin_label: TOOLTIPS["spin_rate"],
                spin_frame: TOOLTIPS["spin_rate"],
                self.general_help_label: GENERAL_HELP,
                self.dirty_status_label: "Shows whether the currently loaded animation config has changes that have not been saved to disk.",
                self.start_button: TOOLTIPS["start"],
                self.stop_button: TOOLTIPS["stop"],
            }
        )
        for entry in (
            *self.start_position_entries,
            *self.search_entries,
            *self.spin_entries,
        ):
            self.tooltips.append(
                ToolTip(
                    entry,
                    (
                        TOOLTIPS["start_position"]
                        if entry in self.start_position_entries
                        else (
                            TOOLTIPS["search_coords"]
                            if entry in self.search_entries
                            else TOOLTIPS["spin_rate"]
                        )
                    ),
                    enabled_callback=self.tooltips_enabled_var.get,
                )
            )
        self._update_mode_help()

    def _load_window_keywords_for_selected_version(self) -> None:
        keywords = get_window_keywords(self.version_var.get(), self.app_settings)
        self.window_keywords_var.set(", ".join(keywords))

    def _install_settings_traces(self) -> None:
        for variable in (
            self.version_var,
            self.config_path_var,
            self.fps_var,
            self.tooltips_enabled_var,
        ):
            variable.trace_add("write", self._save_app_settings)
        self.window_keywords_var.trace_add("write", self._save_app_settings)

    def _save_app_settings(self, *_args: object) -> None:
        self.app_settings.set_launcher_settings(
            version=self.version_var.get(),
            config_path=self.config_path_var.get(),
            fps=self.fps_var.get(),
            tooltips_enabled=self.tooltips_enabled_var.get(),
        )
        keywords = parse_window_keywords(self.window_keywords_var.get())
        if keywords:
            self.app_settings.set_window_keywords_for_version(
                self.version_var.get(), keywords
            )
        save_app_settings(self.app_settings)

    def _on_version_selected(self, _event: tk.Event | None = None) -> None:
        self._load_window_keywords_for_selected_version()
        self._save_app_settings()

    def _window_keywords_or_show_error(self) -> list[str] | None:
        keywords = parse_window_keywords(self.window_keywords_var.get())
        if not keywords:
            log_error("Main", "Window keywords cannot be empty.")
            messagebox.showerror(
                "Invalid window keywords",
                "Enter at least one comma-separated window title keyword.",
            )
            return None
        return keywords

    def _install_console_redirectors(self) -> None:
        sys.stdout = ConsoleRedirector(
            self._original_stdout, self._append_console_message
        )
        sys.stderr = ConsoleRedirector(
            self._original_stderr, self._append_console_message
        )

    def _append_console_message(self, message: str) -> None:
        self.after(0, lambda: self._write_console_message(message))

    def _write_console_message(self, message: str) -> None:
        self.console_text.configure(state="normal")
        self.console_text.insert(tk.END, message)
        self.console_text.see(tk.END)
        self.console_text.configure(state="disabled")

    def _add_tooltips(self, tooltip_map: dict[tk.Widget, str]) -> None:
        for widget, text in tooltip_map.items():
            self.tooltips.append(
                ToolTip(widget, text, enabled_callback=self.tooltips_enabled_var.get)
            )

    def _on_tooltips_toggle(self) -> None:
        self._save_app_settings()
        if not self.tooltips_enabled_var.get():
            for tooltip in self.tooltips:
                tooltip.hide()

    def _bind_auto_apply(self, widget: tk.Widget) -> None:
        widget.bind("<FocusOut>", self._auto_apply_current_edits, add="+")
        widget.bind("<Return>", self._auto_apply_current_edits, add="+")

    def _auto_apply_current_edits(self, _event: tk.Event | None = None) -> None:
        if not self._populating_editor and not self.is_animating:
            self._apply_current_edits(show_errors=False)

    def _on_mode_selected(self, _event: tk.Event | None = None) -> None:
        self._update_mode_help()
        self._auto_apply_current_edits()

    def _update_mode_help(self) -> None:
        mode = self.mode_var.get()
        self.mode_help_label.configure(
            text=MODE_DESCRIPTIONS.get(mode, "Choose an animation mode.")
        )

    def _build_vector_inputs(
        self,
        parent: ttk.Frame,
        variables: list[tk.StringVar],
        labels: tuple[str, str, str],
    ) -> list[ttk.Entry]:
        entries: list[ttk.Entry] = []
        for index, (label, variable) in enumerate(zip(labels, variables, strict=True)):
            ttk.Label(parent, text=label).grid(
                row=0, column=index * 2, sticky="w", padx=(0 if index == 0 else 10, 4)
            )
            entry = ttk.Entry(parent, textvariable=variable, width=10)
            entry.grid(row=0, column=index * 2 + 1, sticky="w")
            self._bind_auto_apply(entry)
            entries.append(entry)
        parent.columnconfigure(len(labels) * 2, weight=1)
        return entries

    def _choose_and_load_config(self) -> None:
        if not self._ensure_stopped_for_edits():
            return
        path = filedialog.askopenfilename(
            title="Load objects.json", filetypes=(("JSON", "*.json"), ("All", "*.*"))
        )
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
        self._set_dirty(False)

    def _save_config(self) -> None:
        if not self._ensure_stopped_for_edits() or not self._apply_current_edits():
            return
        self._write_config(Path(self.config_path_var.get()))

    def _save_config_as(self) -> None:
        if not self._ensure_stopped_for_edits() or not self._apply_current_edits():
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".json", filetypes=(("JSON", "*.json"), ("All", "*.*"))
        )
        if path:
            self.config_path_var.set(path)
            self._write_config(Path(path))

    def _write_config(self, path: Path) -> None:
        path.write_text(
            json.dumps({"objects": self.objects}, indent=2) + "\n", encoding="utf-8"
        )
        self.status_var.set(f"Saved {len(self.objects)} object(s) to {path}.")
        self._set_dirty(False)

    def _refresh_object_list(self) -> None:
        self.object_list.delete(0, tk.END)
        for index, obj in enumerate(self.objects):
            self.object_list.insert(tk.END, obj.get("name") or f"object #{index + 1}")
        if self.current_index is not None and self.current_index < len(self.objects):
            self.object_list.selection_clear(0, tk.END)
            self.object_list.selection_set(self.current_index)
            self.object_list.activate(self.current_index)
            self.object_list.see(self.current_index)

    def _on_object_select(self, _event: tk.Event) -> None:
        if self.is_animating:
            return
        selection = self.object_list.curselection()
        if selection:
            selected_index = selection[0]
            if selected_index == self.current_index:
                return
            if not self._apply_current_edits(show_errors=True):
                self._refresh_object_list()
                return
            self.current_index = selected_index
            self._refresh_object_list()
            self._populate_editor()

    def _populate_editor(self) -> None:
        self._populating_editor = True
        obj = (
            self.objects[self.current_index]
            if self.current_index is not None and self.objects
            else DEFAULT_OBJECT
        )
        self.name_var.set(obj.get("name", ""))
        self.mode_var.set(obj.get("mode", "ping_pong_path"))
        self.start_delay_var.set(str(obj.get("start_delay_seconds", 0)))
        start_position = obj.get("start_position")
        self.use_start_position_var.set(isinstance(start_position, dict))
        self._set_vector_vars(
            self.start_position_vars,
            start_position if isinstance(start_position, dict) else {},
        )
        self._set_vector_vars(self.search_coord_vars, obj.get("search_coords", []))
        self._set_vector_vars(
            self.spin_rate_vars, obj.get("spin_rate_deg_per_sec", [0, 0, 0])
        )
        self._set_text(self.search_text, json.dumps(obj.get("search_coords", [])))
        self._set_text(
            self.waypoints_text, json.dumps(obj.get("waypoints", []), indent=2)
        )
        self._refresh_waypoint_table()
        self._set_text(
            self.spin_text, json.dumps(obj.get("spin_rate_deg_per_sec", [0, 0, 0]))
        )
        self._update_mode_help()
        self._populating_editor = False

    def _set_dirty(self, dirty: bool) -> None:
        self.is_dirty = dirty
        self.dirty_status_var.set("Unsaved changes" if dirty else "Saved")
        marker = "*" if dirty else ""
        self.title(f"{marker}ICR2 Animator Launcher")

    def _set_text(self, widget: tk.Text, value: str) -> None:
        widget.delete("1.0", tk.END)
        widget.insert("1.0", value)

    def _set_vector_vars(self, variables: list[tk.StringVar], values: Any) -> None:
        if isinstance(values, dict):
            values = [values.get(axis, 0) for axis in ("x", "y", "z")]
        elif not isinstance(values, list):
            values = []
        for index, variable in enumerate(variables):
            variable.set(str(values[index]) if index < len(values) else "0")

    def _vector_from_vars(
        self, variables: list[tk.StringVar], label: str
    ) -> list[int | float]:
        values: list[int | float] = []
        for index, variable in enumerate(variables):
            raw_value = variable.get().strip()
            try:
                number = float(raw_value)
            except ValueError as exc:
                axis = ("x", "y", "z")[index]
                raise ValueError(f"{label} {axis} must be numeric.") from exc
            values.append(int(number) if number.is_integer() else number)
        return values

    def _waypoints_from_text(self) -> list[dict[str, Any]] | None:
        try:
            waypoints = json.loads(self.waypoints_text.get("1.0", "end-1c"))
        except json.JSONDecodeError as exc:
            messagebox.showerror("Invalid waypoint JSON", str(exc))
            return None
        if not isinstance(waypoints, list):
            messagebox.showerror(
                "Invalid waypoint JSON", "waypoints must be a JSON list."
            )
            return None
        return waypoints

    def _write_waypoints_to_text(
        self, waypoints: list[dict[str, Any]], selected_index: int | None = None
    ) -> None:
        self._set_text(self.waypoints_text, json.dumps(waypoints, indent=2))
        self._refresh_waypoint_table(selected_index)

    def _selected_waypoint_index(self) -> int | None:
        selected = self.waypoint_table.selection()
        if not selected:
            return None
        return self.waypoint_table.index(selected[0])

    def _refresh_waypoint_table(self, selected_index: int | None = None) -> None:
        for item in self.waypoint_table.get_children():
            self.waypoint_table.delete(item)
        try:
            waypoints = json.loads(self.waypoints_text.get("1.0", "end-1c") or "[]")
        except json.JSONDecodeError:
            return
        if not isinstance(waypoints, list):
            return
        for waypoint in waypoints:
            if isinstance(waypoint, dict):
                values = [waypoint.get(column, "") for column in WAYPOINT_COLUMNS]
            else:
                values = [""] * len(WAYPOINT_COLUMNS)
            self.waypoint_table.insert("", "end", values=values)
        children = self.waypoint_table.get_children()
        if selected_index is not None and children:
            selected_index = max(0, min(selected_index, len(children) - 1))
            self.waypoint_table.selection_set(children[selected_index])
            self.waypoint_table.focus(children[selected_index])

    def _edit_waypoint_cell(self, event: tk.Event) -> None:
        if self.is_animating:
            return
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
        editor = ttk.Entry(self.waypoint_table)
        editor.insert(0, self.waypoint_table.set(item, column))
        editor.select_range(0, tk.END)
        editor.focus_set()
        editor.place(x=x, y=y, width=width, height=height)

        committed = False

        def commit(_event: tk.Event | None = None) -> None:
            nonlocal committed
            if committed:
                return
            committed = True
            self._set_waypoint_value(
                self.waypoint_table.index(item), column, editor.get()
            )
            editor.destroy()

        def cancel(_event: tk.Event | None = None) -> None:
            nonlocal committed
            committed = True
            editor.destroy()

        editor.bind("<Return>", commit)
        editor.bind("<FocusOut>", commit)
        editor.bind("<Escape>", cancel)

    def _set_waypoint_value(self, index: int, column: str, value: str) -> None:
        waypoints = self._waypoints_from_text()
        if waypoints is None or index >= len(waypoints):
            return
        if not isinstance(waypoints[index], dict):
            waypoints[index] = {}
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
        self._write_waypoints_to_text(waypoints, index)
        self._apply_current_edits(show_errors=False)

    def _add_waypoint(self) -> None:
        if not self._ensure_stopped_for_edits():
            return
        waypoints = self._waypoints_from_text()
        if waypoints is None:
            return
        insert_at = self._selected_waypoint_index()
        source = (
            waypoints[insert_at]
            if insert_at is not None
            and waypoints
            and isinstance(waypoints[insert_at], dict)
            else DEFAULT_WAYPOINT
        )
        new_waypoint = copy.deepcopy(source)
        if insert_at is None:
            waypoints.append(new_waypoint)
            insert_at = len(waypoints) - 1
        else:
            insert_at += 1
            waypoints.insert(insert_at, new_waypoint)
        self._write_waypoints_to_text(waypoints, insert_at)
        self._apply_current_edits(show_errors=False)

    def _remove_waypoint(self) -> None:
        if not self._ensure_stopped_for_edits():
            return
        waypoints = self._waypoints_from_text()
        index = self._selected_waypoint_index()
        if waypoints is None or index is None or index >= len(waypoints):
            return
        del waypoints[index]
        self._write_waypoints_to_text(waypoints, index)
        self._apply_current_edits(show_errors=False)

    def _move_waypoint(self, direction: int) -> None:
        if not self._ensure_stopped_for_edits():
            return
        waypoints = self._waypoints_from_text()
        index = self._selected_waypoint_index()
        if waypoints is None or index is None:
            return
        new_index = index + direction
        if not 0 <= new_index < len(waypoints):
            return
        waypoints[index], waypoints[new_index] = waypoints[new_index], waypoints[index]
        self._write_waypoints_to_text(waypoints, new_index)
        self._apply_current_edits(show_errors=False)

    def _apply_current_edits(self, show_errors: bool = True) -> bool:
        if self.is_animating:
            if show_errors:
                messagebox.showinfo(
                    "Animation running", "Stop animation before applying edits."
                )
            return False
        if self.current_index is None:
            return True
        try:
            start_delay = float(self.start_delay_var.get())
            if start_delay < 0:
                raise ValueError("start_delay_seconds must be non-negative")
            search_coords = self._vector_from_vars(
                self.search_coord_vars, "find-object coordinate"
            )
            start_position_values = self._vector_from_vars(
                self.start_position_vars, "start position"
            )
            if self.use_start_position_var.get() and any(
                not isinstance(value, int) for value in start_position_values
            ):
                raise ValueError("start position coordinates must be integers")
            spin_rate = self._vector_from_vars(self.spin_rate_vars, "spin rate")
            waypoints = json.loads(self.waypoints_text.get("1.0", "end-1c"))
            updated = {
                "name": self.name_var.get(),
                "search_coords": search_coords,
                "mode": self.mode_var.get(),
                "start_delay_seconds": (
                    int(start_delay) if start_delay.is_integer() else start_delay
                ),
                "waypoints": waypoints,
                "spin_rate_deg_per_sec": spin_rate,
            }
            if self.use_start_position_var.get():
                updated["start_position"] = {
                    axis: value
                    for axis, value in zip(
                        ("x", "y", "z"), start_position_values, strict=True
                    )
                }
            self._set_text(self.search_text, json.dumps(search_coords))
            self._set_text(self.spin_text, json.dumps(spin_rate))
        except json.JSONDecodeError as exc:
            if show_errors:
                messagebox.showerror("Invalid JSON", str(exc))
            return False
        except ValueError as exc:
            if show_errors:
                messagebox.showerror("Invalid numeric value", str(exc))
            return False
        if self.objects[self.current_index] != updated:
            self.objects[self.current_index] = updated
            self._set_dirty(True)
        self._refresh_object_list()
        return True

    def _add_object(self) -> None:
        if not self._ensure_stopped_for_edits() or not self._apply_current_edits():
            return
        self.objects.append(json.loads(json.dumps(DEFAULT_OBJECT)))
        self._set_dirty(True)
        self.current_index = len(self.objects) - 1
        self._refresh_object_list()
        self._populate_editor()

    def _remove_object(self) -> None:
        if not self._ensure_stopped_for_edits() or self.current_index is None:
            return
        del self.objects[self.current_index]
        self._set_dirty(True)
        self.current_index = (
            min(self.current_index, len(self.objects) - 1) if self.objects else None
        )
        self._refresh_object_list()
        self._populate_editor()

    def _start_animation(self) -> None:
        if not self._apply_current_edits():
            return
        errors = validate_object_config(self.objects)
        if errors:
            for error in errors:
                log_error("Main", f"Config validation failed: {error}")
            messagebox.showerror(
                "Config validation error", "\n".join(f"• {error}" for error in errors)
            )
            return
        log_info("Main", f"Config validation passed for {len(self.objects)} object(s).")
        try:
            fps = float(self.fps_var.get())
            if fps <= 0:
                raise ValueError
        except ValueError:
            log_error("Main", f"Invalid FPS value: {self.fps_var.get()!r}.")
            messagebox.showerror("Invalid FPS", "FPS must be a positive number.")
            return
        window_keywords = self._window_keywords_or_show_error()
        if window_keywords is None:
            return
        self.app_settings.set_window_keywords_for_version(
            self.version_var.get(), window_keywords
        )
        self._save_app_settings()
        log_info(
            "Main",
            f"Starting animation from config={self.config_path_var.get()!r}, version={self.version_var.get()}, fps={fps:g}, objects={len(self.objects)}, window_keywords={window_keywords}.",
        )
        self.service = AnimatorService(
            version=self.version_var.get(),
            verbose=True,
            fps=fps,
            window_keywords=window_keywords,
        )
        self.status_var.set("Starting animation...")
        self._set_running_state(True)
        self.worker = threading.Thread(
            target=self._run_service, args=(list(self.objects),), daemon=True
        )
        self.worker.start()

    def _run_service(self, objects: list[dict[str, Any]]) -> None:
        try:
            if self.service:
                self.service.start(objects)
                self.service.wait()
        except Exception as exc:
            log_error("Main", f"Animator error: {exc}")
            self.after(0, lambda: messagebox.showerror("Animator error", str(exc)))
        finally:
            self.after(0, lambda: self._set_running_state(False))

    def _stop_animation(self) -> None:
        self.status_var.set("Stopping animation...")
        self.stop_button.configure(state="disabled")
        log_info("Main", "Stop animation requested from launcher.")

        service = self.service
        if not service:
            self._set_running_state(False)
            return

        def stop_in_background() -> None:
            error: Exception | None = None
            try:
                service.stop()
            except Exception as exc:
                error = exc
                log_error("Main", f"Animator stop error: {exc}")
            finally:
                self.after(0, lambda: self._finish_stop(error))

        threading.Thread(target=stop_in_background, daemon=True).start()

    def _finish_stop(self, error: Exception | None = None) -> None:
        self._set_running_state(False)
        if error:
            messagebox.showerror("Animator stop error", str(error))

    def _set_running_state(self, running: bool) -> None:
        self.is_animating = running
        edit_state = "disabled" if running else "normal"
        readonly_state = "disabled" if running else "readonly"
        for widget in (
            self.config_entry,
            self.fps_entry,
            self.window_keywords_entry,
            self.name_entry,
            self.start_delay_entry,
            self.search_text,
            self.waypoints_text,
            self.spin_text,
            self.start_position_check,
            *self.start_position_entries,
            *self.search_entries,
            *self.spin_entries,
            self.load_button,
            self.save_button,
            self.save_as_button,
            self.add_button,
            self.remove_button,
            self.add_waypoint_button,
            self.remove_waypoint_button,
            self.move_waypoint_up_button,
            self.move_waypoint_down_button,
            self.tooltips_check,
        ):
            widget.configure(state=edit_state)
        self.version_combo.configure(state=readonly_state)
        self.mode_combo.configure(state=readonly_state)
        self.object_list.configure(state=edit_state)
        self.waypoint_table.state(["disabled"] if running else ["!disabled"])
        self.start_button.configure(state="disabled" if running else "normal")
        self.stop_button.configure(state="normal" if running else "disabled")
        if not running:
            self.status_var.set("Stopped. Load or edit a config, then start animation.")

    def _ensure_stopped_for_edits(self) -> bool:
        if self.is_animating:
            messagebox.showinfo(
                "Animation running", "Stop animation before editing the config."
            )
            return False
        return True

    def _on_close(self) -> None:
        self._save_app_settings()
        if self.service:
            self.service.stop()
        sys.stdout = self._original_stdout
        sys.stderr = self._original_stderr
        self.destroy()


def main() -> None:
    ICR2Launcher().mainloop()


if __name__ == "__main__":
    main()
