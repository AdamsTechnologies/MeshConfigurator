# mesh_config/controllers/settings_controller.py
from __future__ import annotations

import logging
from typing import Optional, List, Dict

from serial.tools import list_ports

# Use package-absolute imports for robustness
from controllers.device_controller import DeviceController
from models.device_model import DeviceModel, MeshChannel

log = logging.getLogger(__name__)


class SettingsController:
    """
    Pragmatic integrator around DeviceController:
      - Detect serial candidates
      - Connect to exactly one device (explicit port preferred)
      - Keep a single owned DeviceController
      - Provide fresh snapshots for the UI
      - Expose last error state for UI
    """

    def __init__(self, explicit_port: Optional[str] = None) -> None:
        self._explicit_port: Optional[str] = explicit_port
        self._dc: Optional[DeviceController] = None
        self._last_error: Optional[Dict] = None

    # ---------------- Detection ----------------

    def detect_candidates(self) -> List[Dict[str, str]]:
        """
        Enumerate serial candidates via pyserial.
        Returns: [{"path": "...", "description": "..."}]
        """
        out: List[Dict[str, str]] = []
        try:
            for p in list_ports.comports():
                desc_parts = [p.description or p.device]
                if p.manufacturer:
                    desc_parts.append(p.manufacturer)
                if p.product and p.product not in desc_parts:
                    desc_parts.append(p.product)
                if p.serial_number:
                    desc_parts.append(f"SN:{p.serial_number}")
                out.append({"path": p.device, "description": " | ".join(desc_parts)})
        except Exception:
            log.exception("detect_candidates failed")
            raise
        return out

    # ---------------- Connect ----------------

    def connect_autodetect_if_single(self) -> Optional[str]:
        """
        Connect logic:
          - If explicit_port was provided at init: try to open it.
          - Else: scan; if exactly 1 candidate -> open it; if 0 or >1 -> set last_error and return None.
        Returns the connected port path on success, else None.
        """
        self._last_error = None

        # If already connected, keep/reuse existing controller
        if self._dc is not None:
            ident = self._dc.identity()
            return ident.get("port") or self._explicit_port

        # Prefer explicit port
        if self._explicit_port:
            try:
                self._dc = DeviceController(port=self._explicit_port)
                return self._explicit_port
            except Exception as e:
                self._last_error = {"code": "open_failed", "detail": str(e)}
                log.exception("Failed to open explicit port %s", self._explicit_port)
                self._dc = None
                return None

        # Autodetect via scan
        try:
            cands = self.detect_candidates()
        except Exception as e:
            # propagate discovery exceptions as open_failed for UI simplicity
            self._last_error = {"code": "open_failed", "detail": str(e)}
            return None

        if len(cands) == 0:
            self._last_error = {"code": "no_candidates", "detail": "No serial devices found"}
            return None
        if len(cands) > 1:
            self._last_error = {"code": "multiple_candidates", "detail": "Multiple serial devices found", "candidates": cands}
            return None

        path = cands[0]["path"]
        try:
            self._dc = DeviceController(port=path)
            return path
        except Exception as e:
            self._last_error = {"code": "open_failed", "detail": str(e)}
            log.exception("Failed to open detected port %s", path)
            self._dc = None
            return None

    def connect_explicit(self, port: str) -> Optional[str]:
        """
        Connect to the provided port and store controller; returns the port on success, else None
        and populates last_error.
        """
        self._last_error = None
        try:
            self._dc = DeviceController(port=port)
            self._explicit_port = port
            return port
        except Exception as e:
            self._last_error = {"code": "open_failed", "detail": str(e)}
            log.exception("Failed to open explicit port %s", port)
            self._dc = None
            return None

    # ---------------- Snapshot / Refresh ----------------

    def fetch_device_model(self, close_after_fetch: bool = False) -> DeviceModel:
        """
        Return a FRESH SettingsModel via DeviceController.snapshot().
        Raises RuntimeError if not connected.
        """
        if self._dc is None:
            raise RuntimeError("Not connected. Call connect_autodetect_if_single() first.")
        model: DeviceModel = self._dc.snapshot()
        if close_after_fetch:
            self.close()
        return model

    def refresh_channels(self) -> List[MeshChannel]:
        """
        Return a FRESH list of MeshChannel (masked PSKs) for pre-apply checks in the UI.
        Raises RuntimeError if not connected.
        """
        if self._dc is None:
            raise RuntimeError("Not connected. Call connect_autodetect_if_single() first.")
        return self._dc.snapshot().MeshChannels

    # ---------------- Errors / Lifecycle ----------------

    def last_error(self) -> Optional[Dict]:
        return self._last_error

    def close(self) -> None:
        if self._dc is not None:
            try:
                self._dc.close()
            except Exception:
                log.debug("DeviceController.close suppressed", exc_info=True)
            finally:
                self._dc = None
