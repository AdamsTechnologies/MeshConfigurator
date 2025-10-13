# app.py
from __future__ import annotations
import os
import time
import logging
import json
import queue
import threading
from typing import Optional, List, Dict, Any

import customtkinter as ctk

from resource_path import resource_path
from models.device_model import DeviceModel
from controllers.device_controller import DeviceController
from controllers.settings_controller import SettingsController
from controllers.preset_controller import PresetController
from ui.confirm_dialog import ConfirmationDialog
from ui.save_preset_dialog import SavePresetDialog
from ui.logging_utils import QueueLogHandler

from ui.panels.base_panel import BasePanel
from ui.panels.device_panel import DevicePanel
from ui.panels.lora_panel import LoRaPanel
from ui.panels.channels_panel import ChannelsPanel
from ui.panels.power_panel import PowerPanel
from ui.panels.position_panel import PositionPanel
from ui.panels.display_panel import DisplayPanel
from ui.panels.bluetooth_panel import BluetoothPanel
from ui.panels.network_panel import NetworkPanel
from ui.panels.modules_panel import ModulesPanel
from ui.port_picker_dialog import PortPickerDialog

log = logging.getLogger(__name__)

class App(ctk.CTk):
    def __init__(self, explicit_port: Optional[str] = None):
        super().__init__()
        # Window
        self.title("Mesh Configurator")
        self.geometry("1300x980")

        try:
            ico = resource_path("favicon.ico")
            self.iconbitmap(ico)  # works with .ico on Windows
        except Exception:
            pass

        # Controllers
        self.settings = SettingsController(explicit_port=explicit_port)
        self._connected_port: Optional[str] = None
        self._orig_model: Optional[DeviceModel] = None

        # Logging -> UI queue
        self.log_q: "queue.Queue[str]" = queue.Queue()
        qh = QueueLogHandler(self.log_q)
        qh.setLevel(logging.INFO)
        # Re-include timestamp and level in log lines
        qh.setFormatter(logging.Formatter("%(asctime)s — %(levelname)s — %(message)s", "%H:%M:%S"))
        root_logger = logging.getLogger()
        try:
            for _h in list(root_logger.handlers):
                root_logger.removeHandler(_h)
        except Exception:
            pass
        root_logger.addHandler(qh)
        root_logger.setLevel(logging.INFO)

        # Layout
        self.columnconfigure(0, weight=0, minsize=520)
        self.columnconfigure(1, weight=2)
        self.rowconfigure(0, weight=1)

        self.left_scroll = ctk.CTkScrollableFrame(self)
        self.left_scroll.grid(row=0, column=0, sticky="nsw", padx=(8, 4), pady=8)
        self.left_scroll.configure(width=520)
        
        self.right = ctk.CTkFrame(self)
        self.right.grid(row=0, column=1, sticky="nsew", padx=(4, 8), pady=8)
        self.right.columnconfigure(0, weight=1)
        self.right.rowconfigure(3, weight=1)

        # Presets bar (row 0)
        self.preset_bar = ctk.CTkFrame(self.right)
        self.preset_bar.grid(row=0, column=0, sticky="ew", padx=8, pady=(6, 8))
        self._build_preset_bar(self.preset_bar)

        # Toolbar (row 1): Connect | Update | Disconnect + status
        self.toolbar = ctk.CTkFrame(self.right)
        self.toolbar.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 4))
        self.btn_detect = ctk.CTkButton(self.toolbar, text="Detect / Connect", command=self._on_detect_clicked)
        self.btn_detect.pack(side="left", padx=(0, 8))
        self.btn_apply = ctk.CTkButton(self.toolbar, text="Update Device", command=self._on_apply_clicked, state="disabled")
        self.btn_apply.pack(side="left", padx=(0, 8))
        self.btn_disconnect = ctk.CTkButton(self.toolbar, text="Disconnect Device", command=self._on_disconnect_clicked, state="disabled")
        self.btn_disconnect.pack(side="left", padx=(0, 8))
        self.status_lbl = ctk.CTkLabel(self.toolbar, text="")
        self.status_lbl.pack(side="right")

        # Info bar (row 2): Device info (left) and Clear Log (right)
        self.info_bar = ctk.CTkFrame(self.right)
        self.info_bar.grid(row=2, column=0, sticky="ew", padx=8, pady=(0, 8))
        self.info_bar.columnconfigure(0, weight=0)
        self.info_bar.columnconfigure(1, weight=1)
        self.device_info_lbl = ctk.CTkLabel(self.info_bar, text="")
        self.device_info_lbl.pack(side="left")
        self.btn_clear_log = ctk.CTkButton(self.info_bar, text="Clear Log", width=100, command=self._on_clear_log)
        self.btn_clear_log.pack(side="right")

        # Log box
        self.log_box = ctk.CTkTextbox(self.right, height=360)
        self.log_box.grid(row=3, column=0, sticky="nsew", padx=8, pady=(0, 8))
        self.log_box.configure(state="disabled")

        # Bottom progress bar under the log box
        self.progress_bar = ctk.CTkProgressBar(self.right, mode="indeterminate")
        self.progress_bar.grid(row=4, column=0, sticky="ew", padx=8, pady=(0, 8))
        self.progress_bar.grid_remove()  # hidden by default
        self._progress_running = False

        # --- PANELS ---
        self.panels: Dict[str, BasePanel] = {}
        self._build_left_sections()

        self.presets = PresetController()
        self._refresh_preset_menu()
        self.after(150, self._poll_logs)

    def _build_preset_bar(self, parent: ctk.CTkFrame):
        parent.columnconfigure(1, weight=1)
        ctk.CTkLabel(parent, text="Presets:", font=ctk.CTkFont(weight="bold")).pack(side="left", padx=(6, 8), pady=6)
        self.preset_menu = ctk.CTkOptionMenu(parent, values=["Load Preset..."], command=self._on_load_preset)
        self.preset_menu.set("Load Preset...")
        self.preset_menu.pack(side="left", padx=(0, 8), pady=6, fill="x", expand=True)
        
        btn_frame = ctk.CTkFrame(parent, fg_color="transparent")
        btn_frame.pack(side="right", padx=(0, 6), pady=6)
        self.btn_save_preset = ctk.CTkButton(btn_frame, text="New", width=90, command=self._on_save_preset)
        self.btn_save_preset.pack(side="left", padx=(0, 4))
        self.btn_rename_preset = ctk.CTkButton(btn_frame, text="Rename", width=90, command=self._on_rename_preset, state="disabled")
        self.btn_rename_preset.pack(side="left", padx=(0, 4))
        self.btn_delete_preset = ctk.CTkButton(btn_frame, text="Delete", width=90, command=self._on_delete_preset, state="disabled")
        self.btn_delete_preset.pack(side="left", padx=(0, 4))

    def _build_left_sections(self):

        panel_classes = [
            DevicePanel, LoRaPanel, ChannelsPanel, PowerPanel, PositionPanel,
            DisplayPanel, BluetoothPanel, NetworkPanel, ModulesPanel
        ]
        for panel_class in panel_classes:
            panel = panel_class(self)
            panel.build(self.left_scroll)
            self.panels[panel.section_title] = panel
    
    # ---------------- Detect / Apply Model ----------------
    def _summarize_apply_report(self, report: dict) -> dict:
        """
        Convert the writer's raw apply report into a cleaner, human-friendly dict.
        - Title-case section names (Power, Position, LoRa, Channels, …)
        - Map statuses to: Success | Error | Timeout | No change
        - Normalize keys: duration_sec, fields_changed, response, errors
        - Keep Channels deletes/upserts nested but clearer
        """
        def pretty_status(s: str | None) -> str:
            if not s:
                return "Unknown"
            s = s.lower()
            return {
                "ok": "Success",
                "error": "Error",
                "timeout": "Timeout",
                "no_change": "No change",
            }.get(s, s.title())

        name_map = {
            "device": "Device",
            "owner": "Owner",
            "lora": "LoRa",
            "power": "Power",
            "position": "Position",
            "display": "Display",
            "bluetooth": "Bluetooth",
            "network": "Network",
            "channels": "Channels",
            "modules": "Modules",
        }

        preferred_order = [
            "device", "owner", "lora", "power", "position", "display",
            "bluetooth", "network", "channels", "modules",
        ]

        sections = report.get("sections", {}) or {}
        errors = report.get("errors", []) or []
        overall_status = pretty_status(report.get("status"))

        # Build ordered output
        out: dict[str, any] = {}
        used = set()
        for key in preferred_order:
            if key in sections:
                used.add(key)
                sec_data = sections[key] or {}
                sec_name = name_map.get(key, key.title())

                if key == "channels":
                    # Special structure for channels
                    chan_out: dict[str, any] = {"status": pretty_status(sec_data.get("status", ""))}
                    # Deleted
                    del_list = []
                    for d in sec_data.get("deleted") or []:
                        del_list.append({
                            "index": d.get("index"),
                            "status": pretty_status(d.get("status")),
                            "duration_sec": d.get("duration_s"),
                            "response": (d.get("stdout") or "")[:4000],
                            "errors": (d.get("stderr") or None),
                        })
                    # Upserts
                    ups_list = []
                    for u in sec_data.get("upserts") or []:
                        ups_list.append({
                            "index": u.get("index"),
                            "status": pretty_status(u.get("status")),
                            "duration_sec": u.get("duration_s"),
                            "fields_changed": u.get("fieldsChanged") or [],
                            "response": (u.get("stdout") or "")[:4000],
                            "errors": (u.get("stderr") or None),
                        })

                    chan_out["deleted"] = del_list
                    chan_out["upserts"] = ups_list
                    out[sec_name] = chan_out
                else:
                    out[sec_name] = {
                        "status": pretty_status(sec_data.get("status")),
                        "duration_sec": sec_data.get("duration_s"),
                        "fields_changed": sec_data.get("fieldsChanged") or [],
                        "response": (sec_data.get("stdout") or "")[:4000],
                        "errors": (sec_data.get("stderr") or None),
                    }

        # Append any sections we didn’t prefer-order
        for key, sec_data in sections.items():
            if key in used:
                continue
            sec_name = name_map.get(key, key.title())
            if key == "channels":
                # (Shouldn't happen if already handled above, but keep consistent)
                chan_out: dict[str, any] = {"status": pretty_status(sec_data.get("status", ""))}
                del_list = [{
                    "index": d.get("index"),
                    "status": pretty_status(d.get("status")),
                    "duration_sec": d.get("duration_s"),
                    "response": (d.get("stdout") or "")[:4000],
                    "errors": (d.get("stderr") or None),
                } for d in (sec_data.get("deleted") or [])]
                ups_list = [{
                    "index": u.get("index"),
                    "status": pretty_status(u.get("status")),
                    "duration_sec": u.get("duration_s"),
                    "fields_changed": u.get("fieldsChanged") or [],
                    "response": (u.get("stdout") or "")[:4000],
                    "errors": (u.get("stderr") or None),
                } for u in (sec_data.get("upserts") or [])]
                chan_out["deleted"] = del_list
                chan_out["upserts"] = ups_list
                out[sec_name] = chan_out
            else:
                out[sec_name] = {
                    "status": pretty_status(sec_data.get("status")),
                    "duration_sec": sec_data.get("duration_s"),
                    "fields_changed": sec_data.get("fieldsChanged") or [],
                    "response": (sec_data.get("stdout") or "")[:4000],
                    "errors": (sec_data.get("stderr") or None),
                }

        # Overall at the bottom
        out["Overall"] = {
            "status": overall_status,
            "errors": errors,
        }
        return out


    def _on_detect_clicked(self):
        self._set_busy(True, "Detecting…")
        threading.Thread(target=self._detect_worker, daemon=True).start()
            
    def _detect_worker(self):
        try:
            self._log("Scanning serial ports…")
            port, candidates = self.settings.auto_connect_or_candidates()
            if port:
                self._connected_port = port
                model = self.settings.fetch_device_model(close_after_fetch=True)
                self._orig_model = model
                self.after(0, lambda m=model: self._on_connected_success(m))
                return

            err = self.settings.last_error() or {}
            if not candidates:
                code = err.get("code")
                if code == "no_candidates":
                    self._log("No serial devices detected. Check your cable or drivers.")
                else:
                    self._log(f"Detect failed: {err}")
                self._set_busy(False, "")
                return

            self._log("Warning: Multiple serial devices detected. Please select your device from the list below.")
            try:
                self._log("Available devices:")
                for c in candidates:
                    self._log(f"- {c.get('product') or c.get('manufacturer') or c.get('description') or c.get('path')} | Port: {c.get('path')} | Score: {c.get('score')}" + (" | BT" if c.get('is_bluetooth') else ""))
            except Exception:
                pass

            def _show_picker():
                def _refresh() -> List[Dict[str, Any]]:
                    try:
                        fresh = self.settings.detect_candidates()
                        self._log("Refreshed device list.")
                        return fresh
                    except Exception as e:
                        self._log(f"Refresh failed: {e}")
                        return []

                def _connect_fn(p: str) -> bool:
                    ok = bool(self.settings.try_connect(p))
                    if ok:
                        self._connected_port = p
                    return ok

                selected = PortPickerDialog.pick_port(self, candidates, _refresh, _connect_fn, default_index=0)
                if selected:
                    try:
                        model = self.settings.fetch_device_model(close_after_fetch=True)
                        self._orig_model = model
                        self._on_connected_success(model)
                    except Exception as e:
                        self._log(f"Failed to read device info after connect: {e}")
                        self._set_busy(False, "")
                else:
                    self._set_busy(False, "")

            self.after(0, _show_picker)
        except Exception as e:
            log.exception("Detect failed")
            self._log(f"Detect failed: {e}")
            self._set_busy(False, "")

    def _on_connected_success(self, model: DeviceModel) -> None:
        try:
            ident = f"{getattr(model.UserInfo,'hwModel',None)} | FW {getattr(model.MetaData,'firmwareVersion',None)}"
            long_name = getattr(model.UserInfo, 'longName', None)
            self._log(
                "Successfully connected.\n"
                f"    Port: {getattr(model.MetaData,'port',None)}\n"
                f"    Device model: {getattr(model.UserInfo,'hwModel',None)}\n"
                f"    Long name: {long_name}\n"
                f"    Device reboots: {getattr(model.MyInfo,'rebootCount',None)}\n"
                f"    Firmware: {getattr(model.MetaData,'firmwareVersion',None)}\n"
            )
            self._apply_model_to_all_panels(model)
            try:
                self._update_device_info(model)
            except Exception:
                pass
            self.btn_apply.configure(state="normal")
            self.btn_disconnect.configure(state="normal")
            self._set_busy(False, f"Connected")
            # self._set_busy(False, f"Connected: {ident}") # displaying the device indentity in the third action row now.
        except Exception:
            self._set_busy(False, "Connected")

    def _on_apply_clicked(self):
        if not self.panels["Channels"].validate_before_apply():
            self._log("Fix validation errors before applying.")
            return
        self._set_busy(True, "Applying…")
        threading.Thread(target=self._apply_worker, daemon=True).start()

    def _on_disconnect_clicked(self):
        """
        Hard-disconnect from any open serial connection, stop workers, reset state,
        and CLEAR all panel forms by applying a blank DeviceModel.
        Safe to call multiple times.
        """
        # Disable button to avoid re-entry
        try:
            self.btn_disconnect.configure(state="disabled")
        except Exception:
            pass

        # Stop any periodic UI jobs
        if hasattr(self, "_refresh_job") and self._refresh_job:
            try:
                self.after_cancel(self._refresh_job)
            except Exception:
                pass
            self._refresh_job = None

        # Stop any in-flight apply worker
        apply_cancel = getattr(self, "_apply_cancel", None)
        if apply_cancel:
            try: apply_cancel.set()
            except Exception: pass
        t = getattr(self, "_apply_thread", None)
        if t and getattr(t, "is_alive", lambda: False)():
            try: t.join(timeout=2.0)
            except Exception: pass

        # Close controllers / iface holders if present
        def _close_obj(obj):
            if not obj:
                return
            for meth in ("disconnect", "close", "_detach_for_cli", "release", "shutdown"):
                if hasattr(obj, meth):
                    try:
                        getattr(obj, meth)()
                    except Exception:
                        pass
            for attr in ("_iface", "iface", "serial", "ser"):
                h = getattr(obj, attr, None)
                if h and hasattr(h, "close"):
                    try:
                        h.close()
                    except Exception:
                        pass

        # Likely holders
        for attr in ("dc", "device_controller", "controller", "writer", "reader", "_iface"):
            _close_obj(getattr(self, attr, None))

        # Reset connection flags/state
        self._connected_port = None
        for attr in ("_orig_model", "_current_model", "_edited_model"):
            setattr(self, attr, None)
        if hasattr(self, "_device_connected"):
            self._device_connected = False

        # CLEAR all panel UIs explicitly via panel.clear_ui() if available
        try:
            for p in getattr(self, "panels", {}).values():
                if hasattr(p, "clear_ui"):
                    try:
                        p.clear_ui()
                    except Exception:
                        pass
        except Exception:
            pass

        # Also apply a blank model to ensure a consistent cleared state
        try:
            blank = self._make_blank_model()
            self._apply_model_to_all_panels(blank)
        except Exception:
            pass

        # UI controls: disable Apply (no model loaded), enable Connect, update status
        try:
            self.btn_apply.configure(state="disabled")
        except Exception:
            pass
        try:
            self.btn_connect.configure(state="normal")
        except Exception:
            pass

        try:
            self._set_busy(False, "Disconnected")
        except Exception:
            pass

        # Re-enable Disconnect for idempotence
        try:
            self.btn_disconnect.configure(state="normal")
        except Exception:
            pass

        try:
            self._log("Disconnected; serial interface closed, state reset, UI cleared.")
            self.btn_apply.configure(state="disabled")
            self.btn_disconnect.configure(state="disabled")
            # Clear device info bar
            if hasattr(self, "device_info_lbl"):
                self.device_info_lbl.configure(text="")
        except Exception:
            pass

    def _apply_worker(self):
        self._log("\nupdating device...")
        self._log("...this may take a few moments...\n")
        try:
            if not self._connected_port or not self._orig_model:
                raise ValueError("Cannot apply, no device model loaded.")

            edited_model = self._build_edited_model(self._orig_model)
            dc = DeviceController(port=self._connected_port)
            summary = dc.apply_from_models(self._orig_model, edited_model)
            model = summary.pop("post_snapshot", None)
            # Ensure we always have a model to re-populate UI, even on no-change or partial reports
            if model is None:
                try:
                    model = dc.reader.snapshot(force_refresh=False)
                except Exception:
                    model = dc.reader.snapshot(force_refresh=True)
            # self._log(json.dumps(summary, indent=2, default=str))
            self._log(json.dumps(self._summarize_apply_report(summary), indent=2, default=str))

            
            if summary.get("errors"):
                self._log(f"Apply finished with errors: {summary['errors']}")
                self._set_busy(False, "Apply finished with errors")
                self._orig_model = model
                self.after(0, lambda: self._apply_model_to_all_panels(model))
            else:
                self._orig_model = model
                self.after(0, lambda: self._apply_model_to_all_panels(model))
                self.after(0, lambda: self._update_device_info(model))
                # If nothing changed, avoid extra refresh work
                if str(summary.get("status") or "").lower() == "no_change":
                    self._set_busy(False, "No changes")
                else:
                    self._log("Update complete. Refreshing device data...")
                    # Kick off a non-blocking refresh loop on the UI thread until channels are ready
                    self.after(0, lambda: self._begin_channels_refresh_retry())

        except Exception as e:
            log.exception("Apply worker failed")
            self._set_busy(False, f"Apply failed: {e}")
            self._log(f"Apply failed: {e}")

    def _apply_model_to_all_panels(self, model: DeviceModel):
        """Iterates through all registered panels and applies the model."""
        for panel in self.panels.values():
            panel.apply_model(model)


    # ---------------- Channel Refresh After Apply ----------------
    def _begin_channels_refresh_retry(self, *, max_attempts: int = 8, interval_ms: int = 800):
        """
        Schedule repeated snapshots until channels are populated, then enable Apply.
        Runs on the Tk main loop via after(); non-blocking.
        """
        try:
            self.status_lbl.configure(text="Refreshing channels…")
            self.btn_apply.configure(state="disabled")
        except Exception:
            pass

        try:
            self._set_busy(True, "Refreshing channels...")
        except Exception:
            pass

        state = {"attempt": 0, "max": max_attempts}

        def _tick():
            try:
                if not self._connected_port:
                    return
                from controllers.device_controller import DeviceController
                dc = DeviceController(port=self._connected_port)
                m = dc.snapshot()
                has_channels = bool(getattr(m, "MeshChannels", None))
                try:
                    dc.close()
                except Exception:
                    pass
                if has_channels:
                    self._orig_model = m
                    self._apply_model_to_all_panels(m)
                    self._set_busy(False, "Applied successfully")
                    try:
                        self.btn_apply.configure(state="normal")
                    except Exception:
                        pass
                    return
            except Exception:
                # swallow and retry
                pass

            state["attempt"] += 1
            if state["attempt"] < state["max"]:
                self.after(interval_ms, _tick)
            else:
                # Give up enabling apply anyway to avoid trapping the user
                self._set_busy(False, "Applied (channels pending)")
                try:
                    self.btn_apply.configure(state="normal")
                except Exception:
                    pass

        self.after(interval_ms, _tick)


    def _build_edited_model(self, base: DeviceModel) -> DeviceModel:
        """Builds an edited DeviceModel by collecting overlays from all panels."""
        m = base.model_copy(deep=True)

        for panel in self.panels.values():
            # Channels panel has a special method, others use the standard overlay
            if isinstance(panel, ChannelsPanel):
                m.MeshChannels = panel.collect_meshchannels()
            else:
                m = panel.collect_model_overlay(m)
        return m

    # ---------------- Presets ----------------

    def _update_preset_button_states(self):
        selected_preset = self.preset_menu.get()
        is_valid = selected_preset and selected_preset != "Load Preset..."
        self.btn_rename_preset.configure(state="normal" if is_valid else "disabled")
        self.btn_delete_preset.configure(state="normal" if is_valid else "disabled")

    def _refresh_preset_menu(self, *, select: str | None = None):
        names = self.presets.get_preset_names()
        values = ["Load Preset..."] + names
        self.preset_menu.configure(values=values)
        self.preset_menu.set(select if select in values else "Load Preset...")
        self._update_preset_button_states()


    def _serialize_app_settings_for_preset(self) -> dict:
        """Gathers settings from all panels to be saved in a preset."""
        data = {}
        for panel in self.panels.values():
            bindings = panel.preset_bindings()
            for section, controls in bindings.items():
                data[section] = {label: var.get() for label, var in controls.items()}
        return data


    def _apply_preset_dict(self, preset: dict):
        """Applies a loaded preset dictionary to the appropriate panels.

        First tries current binding index (visible controls).
        If not found, falls back to panels that declare they support the section
        (e.g., ChannelsPanel for 'Channel N'), allowing creation of new rows.
        """
        preset = preset or {}

        # Build index from panels' current bindings
        section_to_panel: Dict[str, BasePanel] = {}
        for panel in self.panels.values():
            if hasattr(panel, "preset_bindings"):
                try:
                    for sec in panel.preset_bindings().keys():
                        section_to_panel[sec] = panel
                except Exception:
                    pass

        # If the preset contains any channel sections, clear Channels UI first
        try:
            channel_sections = [s for s in preset.keys() if s == "Primary Channel" or str(s).startswith("Channel ")]
            if channel_sections:
                ch_panel = self.panels.get("Channels")
                if ch_panel and hasattr(ch_panel, "clear_ui"):
                    ch_panel.clear_ui()
        except Exception:
            pass

        # Dispatch each section
        for raw_section, fields in preset.items():
            section = str(raw_section).strip()
            dispatched = False

            # Try the binding index first
            panel = section_to_panel.get(section)
            if panel and hasattr(panel, "preset_apply"):
                try:
                    panel.preset_apply({section: fields})
                    dispatched = True
                except Exception as e:
                    self._log(f"Warning: preset section '{section}' apply failed on {panel.section_title}: {e}")

            if not dispatched:
                # Fallback: ask panels if they support this section even if it isn't in bindings yet
                for p in self.panels.values():
                    if hasattr(p, "supports_preset_section") and p.supports_preset_section(section):
                        try:
                            p.preset_apply({section: fields})
                            dispatched = True
                            break
                        except Exception as e:
                            self._log(f"Warning: preset section '{section}' apply failed on {p.section_title}: {e}")

            if not dispatched:
                self._log(f"Warning: No panel found to handle preset section '{section}'")

    def _on_load_preset(self, preset_name: str):
        self._update_preset_button_states()
        if not preset_name or preset_name == "Load Preset...":
            return
            
        # data = self.presets.load_preset(preset_name.strip())
        data = self.presets.load_preset_resolved(preset_name.strip())
        if not data:
            self._log(f"Preset '{preset_name}' not found or empty.")
            self._refresh_preset_menu()
            return
            
        self._apply_preset_dict(data)
        self._log(f"Preset '{preset_name}' loaded.")

    def _on_save_preset(self):
        all_settings = self._serialize_app_settings_for_preset()
        existing = self.presets.get_preset_names()
        result = SavePresetDialog.get_preset_data(self, all_settings, existing)
        if not result:
            self._log("Save cancelled.")
            return
        
        name, selected_data = result
        if not selected_data:
            self._log("Nothing selected to save.")
            return

        # if self.presets.save_preset(name, selected_data):
        if self.presets.save_preset_secure(name, selected_data):
            self._log(f"Preset '{name}' saved.")
            self._refresh_preset_menu(select=name)
        else:
            self._log(f"Failed to save preset '{name}'.")

    def _on_rename_preset(self):
        old_name = self.preset_menu.get()
        if not old_name or old_name == "Load Preset...": return
        
        dialog = ctk.CTkInputDialog(text="Enter new preset name:", title="Rename Preset")
        new_name = dialog.get_input()

        if new_name and new_name.strip() and new_name != old_name:
            if self.presets.rename_preset(old_name, new_name.strip()):
                self._log(f"Preset '{old_name}' renamed to '{new_name}'.")
                self._refresh_preset_menu(select=new_name)
            else:
                self._log(f"Failed to rename preset '{old_name}'.")

    def _on_delete_preset(self):
        preset_name = self.preset_menu.get()
        if not preset_name or preset_name == "Load Preset...": return

        if ConfirmationDialog.ask(self, title="Confirm Deletion",
                                  message=f"Delete the preset '{preset_name}'?"):
            if self.presets.delete_preset(preset_name):
                self._log(f"Preset '{preset_name}' deleted.")
                self._refresh_preset_menu()
            else:
                self._log(f"Failed to delete preset '{preset_name}'.")

    # ---------------- Misc & Logging ----------------

    def _poll_logs(self):
        try:
            while True: self._log(self.log_q.get_nowait())
        except queue.Empty:
            pass
        self.after(200, self._poll_logs)

    def _log(self, s: str):
        try:
            self.log_box.configure(state="normal")
            self.log_box.insert("end", str(s).strip() + "\n")
            self.log_box.see("end")
            self.log_box.configure(state="disabled")
        except Exception:
            pass

    def _on_clear_log(self):
        try:
            self.log_box.configure(state="normal")
            self.log_box.delete("1.0", "end")
            self.log_box.configure(state="disabled")
        except Exception:
            pass

    def _update_device_info(self, model: DeviceModel | None):
        """Show concise device info on the info bar (right side)."""
        try:
            if not model:
                self.device_info_lbl.configure(text="")
                return
            port = getattr(getattr(model, "MetaData", None), "port", None) or ""
            hw = getattr(getattr(model, "UserInfo", None), "hwModel", None) or getattr(getattr(model, "MetaData", None), "hwModel", None) or ""
            fw = getattr(getattr(model, "MetaData", None), "firmwareVersion", None) or ""
            parts = []
            if port: parts.append(f"Port: {port}")
            if hw: parts.append(f"Model: {hw}")
            if fw: parts.append(f"FW: {fw}")
            self.device_info_lbl.configure(text="  |  ".join(parts))
        except Exception:
            try:
                self.device_info_lbl.configure(text="")
            except Exception:
                pass

    def _set_busy(self, busy: bool, status: str = ""):
        self.status_lbl.configure(text=status)
        self.btn_detect.configure(state="disabled" if busy else "normal")
        self.btn_apply.configure(state=("disabled" if busy else "normal") if self._orig_model else "disabled")
        # Bottom progress bar visibility
        try:
            if busy:
                if not self._progress_running:
                    self.progress_bar.grid()
                    self.progress_bar.start()
                    self._progress_running = True
            else:
                if self._progress_running:
                    try:
                        self.progress_bar.stop()
                    except Exception:
                        pass
                    self.progress_bar.grid_remove()
                    self._progress_running = False
        except Exception:
            pass


    def _make_blank_model(self) -> DeviceModel:
        """
        Construct a minimal 'blank' DeviceModel that causes panels to clear their fields.
        Uses pydantic's validation if available; falls back to a deep-copied shell.
        """
        try:
            # Pydantic v2 style; fills optionals with defaults/None
            return DeviceModel.model_validate({
                "Device": {},
                "UserInfo": {},
                "Lora": {},
                "Power": {},
                "Position": {},
                "Display": {},
                "BlueTooth": {},
                "Network": {},
                "ModuleConfig": {},
                "MeshChannels": [],
                "MetaData": {},
                "MyInfo": {},
            })
        except Exception:
            # Fallback: deep copy whatever we last had and blank the sections
            try:
                if getattr(self, "_orig_model", None) is not None:
                    blank = self._orig_model.model_copy(deep=True)
                    for sec in ("Device","UserInfo","Lora","Power","Position","Display",
                                "BlueTooth","Network","ModuleConfig","MetaData","MyInfo"):
                        if hasattr(blank, sec):
                            try:
                                # Replace dict-like/objects with empty instance, else set None
                                cur = getattr(blank, sec)
                                if isinstance(cur, (dict, list, set, tuple)):
                                    setattr(blank, sec, type(cur)())
                                else:
                                    setattr(blank, sec, None)
                            except Exception:
                                setattr(blank, sec, None)
                    setattr(blank, "MeshChannels", [])
                    return blank
            except Exception:
                pass
            # Last ditch: hope defaults work
            return DeviceModel()
        
        
if __name__ == "__main__":
    # Logging is routed to the UI; avoid console basicConfig
    ctk.set_appearance_mode("dark")
    app = App()
    app.mainloop()

