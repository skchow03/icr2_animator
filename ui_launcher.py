"""Tkinter launcher for configuring and starting ICR2 object animations."""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from animator_service import AnimatorService
from config_validation import validate_object_config
from icr2_versions import DEFAULT_ICR2_VERSION, KNOWN_ICR2_VERSIONS


class AnimatorLauncher(tk.Tk):
    """Small UI for selecting an ICR2 version/config and controlling animation."""

    def __init__(self):
        super().__init__()
        self.title("ICR2 Object Animator")
        self.resizable(False, False)

        self.service: AnimatorService | None = None
        self.worker: threading.Thread | None = None
        self.version_var = tk.StringVar(value=DEFAULT_ICR2_VERSION)
        self.config_var = tk.StringVar(value="objects.json")
        self.status_var = tk.StringVar(value="Select a version and config, then start.")

        self._build_widgets()
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
        self.config_entry.bind("<KeyRelease>", lambda _event: self._update_start_state())
        ttk.Button(frame, text="Browse...", command=self._browse_config).grid(row=1, column=2, **padding)

        self.start_button = ttk.Button(frame, text="Start", command=self._start)
        self.start_button.grid(row=2, column=1, sticky="e", **padding)
        self.stop_button = ttk.Button(frame, text="Stop", command=self._stop, state="disabled")
        self.stop_button.grid(row=2, column=2, sticky="e", **padding)

        ttk.Label(frame, textvariable=self.status_var).grid(
            row=3, column=0, columnspan=3, sticky="w", **padding
        )

    def _browse_config(self):
        path = filedialog.askopenfilename(
            title="Select object config",
            filetypes=(("JSON files", "*.json"), ("All files", "*.*")),
        )
        if path:
            self.config_var.set(path)
            self._update_start_state()

    def _update_start_state(self):
        can_start = bool(self.version_var.get() in KNOWN_ICR2_VERSIONS and self.config_var.get())
        if self.service and self.service.is_running():
            can_start = False
        self.start_button.configure(state="normal" if can_start else "disabled")

    def _start(self):
        selected_version = self.version_var.get()
        config_path = self.config_var.get()
        self.service = AnimatorService(version=selected_version, verbose=True)

        try:
            objects = self.service.load_objects(config_path)
        except Exception as exc:
            messagebox.showerror("Config error", str(exc))
            self.service = None
            self._update_start_state()
            return

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
