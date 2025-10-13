# ui/panels/channels_panel.py
from __future__ import annotations
import customtkinter as ctk
from typing import Dict, Any, List
from ui.common import make_collapsible, is_base64ish
from .base_panel import BasePanel
from ui.channel_frame import ChannelFrame
from models.device_model import MeshChannel

class ChannelsPanel(BasePanel):
    section_title = "Channels"

    def __init__(self, app: "ctk.CTk"):
        super().__init__(app)
        self._channel_frames: List[ChannelFrame] = []
        self._channels_container: ctk.CTkFrame | None = None
        self.btn_add_channel: ctk.CTkButton | None = None


    def build(self, parent: ctk.CTkFrame):
        _, self.frame, _ = make_collapsible(parent, "Channels", open=True)

        primary = ChannelFrame(self.frame, index=0, is_primary=True)
        primary.pack(fill="x", padx=6, pady=4)
        self._channel_frames.append(primary)

        add_hdr = ctk.CTkFrame(self.frame, fg_color="transparent")
        add_hdr.pack(fill="x", padx=6, pady=(10, 2))
        ctk.CTkLabel(add_hdr, text="Additional Channels", font=ctk.CTkFont(weight="bold")).pack(side="left")
        self.btn_add_channel = ctk.CTkButton(add_hdr, text="+ Add Channel", command=self._on_add_channel_clicked, width=140)
        self.btn_add_channel.pack(side="right")

        self._channels_container = ctk.CTkFrame(self.frame, fg_color="transparent")
        self._channels_container.pack(fill="x", expand=True, pady=0, padx=0)


    def supports_preset_section(self, section: str) -> bool:
        s = str(section).strip()
        return s == "Primary Channel" or s.startswith("Channel ")


    def apply_model(self, model) -> None:
        """
        In-place diff update of channel rows to match model.MeshChannels.
        - Primary (index 0) row is preserved and updated.
        - Secondary rows are added/updated/removed to match target indices.
        - If channels are empty, do nothing (avoid wiping during async refresh).
        """
        channels = getattr(model, "MeshChannels", None) or []
        # If no channels are present, avoid wiping the UI; device may not have delivered them yet
        if len(channels) == 0:
            return

        # Ensure primary row exists and update it
        cf0 = self._get_channel_frame(0)
        if cf0 is None:
            cf0 = ChannelFrame(self.frame, index=0, is_primary=True)
            cf0.pack(fill="x", padx=6, pady=4)
            self._channel_frames.append(cf0)
        ch0 = next((c for c in channels if getattr(c, "index", 0) == 0), None)
        self._apply_channel_to_frame(cf0, ch0)

        # Build target set of secondary indices from model
        target = sorted({int(getattr(c, "index", 0)) for c in channels if int(getattr(c, "index", 0)) >= 1 and self._is_nonempty_channel(c)})
        current = sorted([cf.index for cf in self._channel_frames if cf.index >= 1 and cf.winfo_exists()])

        # Remove secondary frames not in target
        for idx in current:
            if idx not in target:
                self._delete_channel_row(idx)

        # Add missing secondary frames and update all existing ones to model values
        for idx in target:
            cf = self._get_channel_frame(idx)
            if cf is None:
                self._add_channel_row(index=idx)
                cf = self._get_channel_frame(idx)
            if cf is not None:
                ch = next((c for c in channels if int(getattr(c, "index", 0)) == idx), None)
                self._apply_channel_to_frame(cf, ch)


    def _apply_channel_to_frame(self, cf: ChannelFrame | None, ch: MeshChannel | None):
        if not cf: return

        if ch:
            cf.name_var.set(ch.name or "")
            prec = int(getattr(ch, "position_precision", 0) or 0)
            cf.gps_var.set(prec > 0)
            cf.precision_var.set(str(prec))
            cf.uplink_var.set(bool(getattr(ch, "uplink_enabled", False)))
            cf.downlink_var.set(bool(getattr(ch, "downlink_enabled", False)))
            cf.key_var.set(str(getattr(ch, "psk", "")))
        else:
            cf.name_var.set("")
            cf.gps_var.set(False)
            cf.precision_var.set("0")
            cf.uplink_var.set(False)
            cf.downlink_var.set(False)
            cf.key_var.set("")

        if hasattr(cf, "update_gps_enabled"): cf.update_gps_enabled()
        if hasattr(cf, "update_psk_preview"): cf.update_psk_preview()


    def collect_meshchannels(self) -> List[MeshChannel]:
        out: List[MeshChannel] = []
        # Renumber secondaries sequentially (1..N) to avoid stale indices from presets/old UI
        sec_idx = 1
        for cf in sorted(self._channel_frames, key=lambda f: f.index):
            if not cf.winfo_exists():
                continue

            precision = int(cf.precision_var.get().strip() or "0") if cf.gps_var.get() else 0
            psk_val = cf.key_var.get().strip()

            idx = 0 if cf.index == 0 else sec_idx
            if cf.index != 0:
                sec_idx += 1

            out.append(MeshChannel(
                index=idx,
                name=(cf.name_var.get().strip() or None),
                uplink_enabled=cf.uplink_var.get(),
                downlink_enabled=cf.downlink_var.get(),
                position_precision=precision,
                psk=psk_val,
                psk_present=is_base64ish(psk_val)
            ))
        return out


    def preset_bindings(self) -> Dict[str, Dict[str, Any]]:
        bindings = {}
        for cf in self._channel_frames:
            if not cf.winfo_exists(): continue
            
            section_name = "Primary Channel" if cf.index == 0 else f"Channel {cf.index}"
            bindings[section_name] = {
                "Name": cf.name_var, "PSK": cf.key_var, "Precision (0..32)": cf.precision_var,
                "Uplink": cf.uplink_var, "Downlink": cf.downlink_var, "Default Public": cf.default_public_var
            }
        return bindings


    def preset_apply(self, section_fields: Dict[str, Any]):
        for section, fields in section_fields.items():
            idx = -1
            if section == "Primary Channel": idx = 0
            elif section.startswith("Channel "):
                try: idx = int(section.split(" ")[1])
                except (ValueError, IndexError): continue
            
            if idx >= 1: # Auto-add any non-primary channel from a preset
                cf = self._get_channel_frame(idx)
                if not cf:
                    self._add_channel_row(index=idx)
                    cf = self._get_channel_frame(idx)
                if cf:
                    self._apply_fields_to_frame(cf, fields)
            elif idx == 0: # Apply to existing primary
                cf = self._get_channel_frame(0)
                if cf:
                    self._apply_fields_to_frame(cf, fields)
    

    def _apply_fields_to_frame(self, cf: ChannelFrame, fields: Dict[str, Any]):
        key_to_var_map = {
            "Name": "name_var", "PSK": "key_var", "Precision (0..32)": "precision_var",
            "Uplink": "uplink_var", "Downlink": "downlink_var", "Default Public": "default_public_var"
        }
        for key, value in fields.items():
            var_name = key_to_var_map.get(key)
            if var_name and hasattr(cf, var_name):
                var_object = getattr(cf, var_name)
                val_str = str(value or "")
                if isinstance(var_object, ctk.BooleanVar):
                    var_object.set(val_str.lower() in ('true', '1'))
                else:
                    var_object.set(val_str)
        
        if hasattr(cf, "update_gps_enabled"): cf.update_gps_enabled()
        if hasattr(cf, "update_psk_preview"): cf.update_psk_preview()


    def clear_ui(self):
        """Clear all channel UI: reset primary row and remove all secondary rows."""
        # Reset primary
        cf0 = self._get_channel_frame(0)
        if cf0 is None:
            # If for some reason primary is missing, create a fresh one
            cf0 = ChannelFrame(self.frame, index=0, is_primary=True)
            cf0.pack(fill="x", padx=6, pady=4)
            self._channel_frames.append(cf0)
        # Apply empty model to primary
        self._apply_channel_to_frame(cf0, None)

        # Remove all secondary frames
        for cf in list(self._channel_frames):
            if cf.index >= 1 and cf.winfo_exists():
                try:
                    cf.destroy()
                except Exception:
                    pass
        # Keep only the primary reference
        self._channel_frames = [cf for cf in self._channel_frames if cf.index == 0 and cf.winfo_exists()]


    def _on_add_channel_clicked(self):
        existing_indices = {cf.index for cf in self._channel_frames}
        # Next index starts searching from 1
        next_idx = 1
        while next_idx in existing_indices:
            next_idx += 1
        self._add_channel_row(index=next_idx)


    def _add_channel_row(self, *, index: int, model: MeshChannel | None = None):
        new_cf = ChannelFrame(self._channels_container, index=index, delete_callback=lambda idx=index: self._delete_channel_row(idx))
        new_cf.pack(fill="x", padx=6, pady=4)
        self._channel_frames.append(new_cf)
        if model:
            self._apply_channel_to_frame(new_cf, model)


    def _delete_channel_row(self, index_to_delete: int):
        frame_to_delete = self._get_channel_frame(index_to_delete)
        if frame_to_delete:
            frame_to_delete.destroy()
            self._channel_frames = [cf for cf in self._channel_frames if cf.index != index_to_delete]


    def _get_channel_frame(self, index: int) -> ChannelFrame | None:
        for cf in self._channel_frames:
            if cf.index == index and cf.winfo_exists():
                return cf
        return None


    def _is_nonempty_channel(self, ch: MeshChannel | None) -> bool:
        if not ch: return False
        name = (getattr(ch, "name", None) or "").strip()
        prec = int(getattr(ch, "position_precision", 0) or 0)
        up = bool(getattr(ch, "uplink_enabled", False))
        down = bool(getattr(ch, "downlink_enabled", False))
        psk = bool(getattr(ch, "psk_present", False))
        return bool(name or prec or up or down or psk)


    def validate_before_apply(self) -> bool:
        for cf in self._channel_frames:
            if cf.winfo_exists():
                psk = cf.key_var.get().strip()
                if psk and not is_base64ish(psk):
                    label = "Primary Channel" if cf.index == 0 else f"Channel {cf.index}"
                    self.app._log(f"Error: {label} PSK must be valid base64.")
                    return False
        return True
