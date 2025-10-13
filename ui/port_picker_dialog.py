from __future__ import annotations
from typing import List, Dict, Any, Callable, Optional
import customtkinter as ctk


class PortPickerDialog(ctk.CTkToplevel):
    """
    Modal dialog to present available serial ports and allow user selection.
    - Displays friendly multi-line details per candidate
    - Provides Refresh and Connect actions
    - Calls provided callbacks for refresh/connect
    """

    def __init__(
        self,
        parent,
        candidates: List[Dict[str, Any]],
        refresh_fn: Callable[[], List[Dict[str, Any]]],
        connect_fn: Callable[[str], bool],
        default_index: int = 0,
    ) -> None:
        super().__init__(master=parent)
        self.title("Select Serial Device")
        self.resizable(True, True)
        self.transient(parent)
        self.grab_set()

        self._refresh_fn = refresh_fn
        self._connect_fn = connect_fn
        self._candidates: List[Dict[str, Any]] = candidates or []
        self._selected_index = max(0, min(default_index, max(0, len(self._candidates) - 1)))
        self.result: Optional[str] = None  # Connected port path on success

        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

        # Layout
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # Header
        header = ctk.CTkLabel(self, text="Multiple serial devices detected. Select your device:", font=ctk.CTkFont(size=14, weight="bold"))
        header.grid(row=0, column=0, sticky="w", padx=10, pady=(10, 4))

        # List
        self.list_frame = ctk.CTkScrollableFrame(self)
        self.list_frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=6)
        self.list_frame.grid_columnconfigure(0, weight=1)

        self._choice_var = ctk.IntVar(value=self._selected_index)
        self._populate_list()

        # Error/status
        self._status = ctk.CTkLabel(self, text="", text_color="tomato")
        self._status.grid(row=2, column=0, sticky="w", padx=10, pady=(2, 0))

        # Actions
        actions = ctk.CTkFrame(self)
        actions.grid(row=3, column=0, sticky="ew", padx=10, pady=(6, 10))
        actions.grid_columnconfigure(0, weight=1)
        btn_wrap = ctk.CTkFrame(actions)
        btn_wrap.grid(row=0, column=0, sticky="e")
        self._btn_connect = ctk.CTkButton(btn_wrap, text="Connect", command=self._on_connect)
        self._btn_connect.pack(side="right", padx=(6, 0))
        self._btn_refresh = ctk.CTkButton(btn_wrap, text="Refresh", command=self._on_refresh)
        self._btn_refresh.pack(side="right")
        self._btn_cancel = ctk.CTkButton(btn_wrap, text="Cancel", command=self._on_cancel)
        self._btn_cancel.pack(side="right", padx=(0, 6))

        self.bind("<Return>", lambda _e: self._on_connect())
        self.bind("<Escape>", lambda _e: self._on_cancel())

        self._center_over_parent(parent, width=640, height=520)

    def _populate_list(self) -> None:
        for w in list(self.list_frame.winfo_children()):
            try:
                w.destroy()
            except Exception:
                pass
        for idx, c in enumerate(self._candidates):
            rb = ctk.CTkRadioButton(self.list_frame, text="", variable=self._choice_var, value=idx)
            rb.grid(row=idx * 2, column=0, sticky="w")
            details = c.get("friendly") or self._format_friendly(c)
            lbl = ctk.CTkLabel(self.list_frame, text=details, justify="left")
            lbl.grid(row=idx * 2 + 1, column=0, sticky="w", padx=(24, 0), pady=(0, 6))

    def _format_friendly(self, c: Dict[str, Any]) -> str:
        parts: List[str] = []
        title = c.get("product") or c.get("manufacturer") or c.get("description") or c.get("path") or ""
        parts.append(str(title))
        parts.append(f"Port: {c.get('path','')}")
        manu = c.get("manufacturer")
        if manu:
            parts.append(f"Manufacturer: {manu}")
        sn = c.get("serial_number")
        if sn:
            parts.append(f"Serial: {sn}")
        return "\n".join(parts)

    def _on_refresh(self) -> None:
        try:
            fresh = self._refresh_fn() or []
            self._candidates = fresh
            # Keep previous selection if still present
            sel_path = None
            try:
                sel_path = self._candidates[self._choice_var.get()].get("path")
            except Exception:
                pass
            new_index = 0
            if sel_path:
                for i, c in enumerate(self._candidates):
                    if c.get("path") == sel_path:
                        new_index = i
                        break
            self._choice_var.set(new_index)
            self._populate_list()
            self._status.configure(text="")
        except Exception as e:
            self._status.configure(text=str(e))

    def _on_connect(self) -> None:
        try:
            idx = int(self._choice_var.get())
            cand = self._candidates[idx]
            port = cand.get("path")
            if not port:
                self._status.configure(text="Select a port.")
                return
            # Run connect asynchronously to avoid freezing UI
            self._set_busy(True, msg=f"Connecting to {port}…")

            def _work():
                ok = False
                try:
                    ok = bool(self._connect_fn(str(port)))
                except Exception:
                    ok = False
                def _done():
                    if ok:
                        self.result = str(port)
                        self.destroy()
                    else:
                        self._status.configure(text=f"Failed to connect on {port}. Please select another port or retry.")
                        self._set_busy(False)
                try:
                    self.after(0, _done)
                except Exception:
                    pass

            import threading as _threading
            _threading.Thread(target=_work, daemon=True).start()
        except Exception as e:
            self._status.configure(text=str(e))

    def _set_busy(self, busy: bool, msg: Optional[str] = None) -> None:
        try:
            state = "disabled" if busy else "normal"
            self._btn_connect.configure(state=state)
            self._btn_refresh.configure(state=state)
            self._btn_cancel.configure(state=state)
            if msg is not None:
                self._status.configure(text=msg if busy else "")
        except Exception:
            pass

    def _on_cancel(self) -> None:
        self.result = None
        self.destroy()

    def _center_over_parent(self, parent, width: int = 640, height: int = 520) -> None:
        try:
            self.update_idletasks()
            px = parent.winfo_rootx()
            py = parent.winfo_rooty()
            pw = parent.winfo_width()
            ph = parent.winfo_height()
            x = px + (pw // 2) - (width // 2)
            y = py + (ph // 2) - (height // 2)
            self.geometry(f"{width}x{height}+{max(0, x)}+{max(0, y)}")
        except Exception:
            self.geometry(f"{width}x{height}")

    @staticmethod
    def pick_port(
        parent,
        candidates: List[Dict[str, Any]],
        refresh_fn: Callable[[], List[Dict[str, Any]]],
        connect_fn: Callable[[str], bool],
        default_index: int = 0,
    ) -> Optional[str]:
        dlg = PortPickerDialog(parent, candidates=candidates, refresh_fn=refresh_fn, connect_fn=connect_fn, default_index=default_index)
        parent.wait_window(dlg)
        return dlg.result
