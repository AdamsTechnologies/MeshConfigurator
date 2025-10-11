# ui/port_select_dialog.py
from __future__ import annotations

from typing import List, Dict, Optional, Tuple
import customtkinter as ctk


class PortSelectDialog(ctk.CTkToplevel):
    """Modal dialog to select a serial port from multiple candidates.

    candidates: List[{"path": str, "description": str}]
    returns selected path or None
    """

    def __init__(self, parent, candidates: List[Dict[str, str]]):
        super().__init__(parent)
        self.title("Select Serial Port")
        self.transient(parent)
        self.grab_set()

        # Result
        self.result: Optional[str] = None
        self._candidates = candidates

        ctk.CTkLabel(self, text="Multiple serial devices found. Please choose one:",
                     wraplength=420, justify="left").pack(padx=18, pady=(16, 10), anchor="w")

        # Build a dropdown with path + description
        values: List[str] = []
        self._value_to_path: Dict[str, str] = {}
        for c in candidates:
            label = f"{c.get('path','')} — {c.get('description','')}"
            values.append(label)
            self._value_to_path[label] = c.get("path", "")

        self._option = ctk.CTkOptionMenu(self, values=values, width=460)
        if values:
            self._option.set(values[0])
        self._option.pack(padx=18, pady=(0, 8), fill="x")

    # Remember selection checkbox
        self._remember_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(self, text="Remember this port for next time",
                        variable=self._remember_var).pack(padx=18, pady=(0, 14), anchor="w")

        # Buttons
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(padx=18, pady=(0, 16), fill="x")
        ctk.CTkButton(btn_frame, text="Cancel", command=self._on_cancel, width=120).pack(side="right", padx=(6, 0))
        ctk.CTkButton(btn_frame, text="Connect", command=self._on_connect, width=120).pack(side="right", padx=(0, 6))

        # Center after layout
        self.after(50, self._center_window)

    def _center_window(self):
        try:
            self.update_idletasks()
            width = self.winfo_width()
            height = self.winfo_height()
            x = self.master.winfo_x() + (self.master.winfo_width() // 2) - (width // 2)
            y = self.master.winfo_y() + (self.master.winfo_height() // 2) - (height // 2)
            self.geometry(f"{width}x{height}+{x}+{y}")
        except Exception:
            pass

    def _on_cancel(self):
        self.result = None
        self.destroy()

    def _on_connect(self):
        sel = self._option.get()
        self.result = self._value_to_path.get(sel)
        self.destroy()

    @classmethod
    def ask(cls, parent, candidates: List[Dict[str, str]]) -> Tuple[Optional[str], bool]:
        dlg = cls(parent, candidates)
        parent.wait_window(dlg)
        return dlg.result, bool(dlg._remember_var.get())
