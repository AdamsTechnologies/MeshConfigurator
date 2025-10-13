# mesh_config/controllers/settings_controller.py
from __future__ import annotations

import os
import logging
from typing import Optional, List, Dict, Any, Tuple

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
        # Persistence for last good port
        self._cache_path = self._get_cache_path()
        self._last_good_port: Optional[str] = self._load_last_good_port()
        self._probe_recent: Dict[str, float] = {}

    # ---------------- Detection ----------------

    def detect_candidates(self) -> List[Dict[str, Any]]:
        """
        Enumerate serial candidates via pyserial with rich metadata and a heuristic score.
        Returns: list of dicts with keys:
          path, description, manufacturer, product, serial_number, vid, pid, hwid,
          is_bluetooth, is_legacy, score
        """
        out: List[Dict[str, Any]] = []
        try:
            ports = list_ports.comports()
        except Exception:
            log.exception("detect_candidates failed")
            raise

        for p in ports:
            try:
                dev = p.device
                desc = p.description or dev
                manu = getattr(p, "manufacturer", None)
                prod = getattr(p, "product", None)
                sn = getattr(p, "serial_number", None)
                vid = getattr(p, "vid", None)
                pid = getattr(p, "pid", None)
                hwid = getattr(p, "hwid", None)
                iface = getattr(p, "interface", None)

                # Heuristics
                is_bluetooth = any(
                    s and ("bluetooth" in s.lower()) for s in [desc, manu, prod, iface]
                )
                is_legacy = False
                try:
                    # COM1 is often a legacy/virtual port; don't prioritize it
                    if os.name == "nt" and str(dev).upper().startswith("COM1"):
                        is_legacy = True
                except Exception:
                    pass

                score = self._score_port(dev, vid, pid, desc, manu, prod, is_bluetooth, is_legacy)

                # Friendly description
                parts: List[str] = []
                if prod:
                    parts.append(str(prod))
                elif manu:
                    parts.append(str(manu))
                else:
                    parts.append(str(desc))
                parts.append(f"Port: {dev}")
                if manu:
                    parts.append(f"Manufacturer: {manu}")
                if sn:
                    parts.append(f"Serial: {sn}")

                out.append({
                    "path": dev,
                    "description": " | ".join([x for x in [desc, manu, prod] if x]),
                    "manufacturer": manu,
                    "product": prod,
                    "serial_number": sn,
                    "vid": vid,
                    "pid": pid,
                    "hwid": hwid,
                    "is_bluetooth": bool(is_bluetooth),
                    "is_legacy": bool(is_legacy),
                    "score": score,
                    "friendly": "\n".join(parts),
                })
            except Exception:
                log.debug("Skipping port due to parse error", exc_info=True)
                continue

        # On macOS: prefer /dev/tty.* over /dev/cu.* by nudging score
        try:
            import platform as _platform
            if _platform.system().lower() == "darwin":
                for c in out:
                    dev = c.get("path") or ""
                    if str(dev).startswith("/dev/tty."):
                        c["score"] = int(c.get("score", 0)) + 5
                    if str(dev).startswith("/dev/cu."):
                        c["score"] = int(c.get("score", 0)) - 2
        except Exception:
            pass

        # On Windows: downrank Bluetooth significantly
        if os.name == "nt":
            for c in out:
                if c.get("is_bluetooth"):
                    c["score"] = int(c.get("score", 0)) - 25

        # Sort by score descending
        out.sort(key=lambda d: int(d.get("score", 0)), reverse=True)
        return out

    def _score_port(
        self,
        dev: str,
        vid: Optional[int],
        pid: Optional[int],
        desc: Optional[str],
        manu: Optional[str],
        prod: Optional[str],
        is_bluetooth: bool,
        is_legacy: bool,
    ) -> int:
        score = 0
        # USB-style with VID/PID gets a strong boost
        if vid is not None and pid is not None:
            score += 50
        # Known bridge vendors
        known_vendors = {0x10C4, 0x1A86, 0x0403, 0x2E8A}  # CP210x, CH340, FTDI, Raspberry Pi Pico
        if vid in known_vendors:
            score += 20
        # Linux ttyUSB/ACM style
        try:
            if str(dev).startswith("/dev/ttyUSB") or str(dev).startswith("/dev/ttyACM"):
                score += 10
        except Exception:
            pass
        # macOS tty.* preferred vs cu.* (applied later as well)
        try:
            if str(dev).startswith("/dev/tty."):
                score += 5
        except Exception:
            pass
        # Bluetooth and obvious legacy downranks
        if is_bluetooth:
            score -= 30
        if is_legacy:
            score -= 15
        # Mild boost if description contains typical strings
        s = " ".join([x for x in [desc or "", manu or "", prod or ""]])
        if any(k in s.lower() for k in ["usb", "uart", "cp210", "ch340", "ftdi", "silicon labs", "serial"]):
            score += 5
        return score

    # ---------------- Advanced connect flow ----------------

    def try_connect(self, port: str) -> Optional[str]:
        """Attempt to connect to a specific port and hold the controller on success."""
        try:
            self._dc = DeviceController(port=port)
            self._save_last_good_port(port)
            return port
        except Exception as e:
            self._last_error = {"code": "open_failed", "detail": str(e)}
            # Downgrade to warning without traceback to avoid alarming end-users
            log.warning("Failed to open port %s", port)
            self._dc = None
            return None

    def _probe_port(self, port: str, timeout_s: float = 2.5) -> bool:
        """
        Lightweight probe to see if a Meshtastic interface responds.
        Tries to instantiate a temporary DeviceController and call identity().
        Returns True if identity appears valid.
        """
        import threading as _threading
        import time as _time

        # Skip if probed recently to avoid churn/port contention
        try:
            last = float(self._probe_recent.get(port, 0.0))
            if _time.monotonic() - last < 5.0:
                return False
        except Exception:
            pass
        result = {"ok": False}

        def _worker():
            try:
                dc = DeviceController(port=port)
                ident = dc.identity(silent=True) or {}
                # Heuristic: hwModel or firmwareVersion present => likely Meshtastic
                if any(ident.get(k) for k in ("hwModel", "firmwareVersion")):
                    result["ok"] = True
            except Exception:
                pass
            finally:
                try:
                    dc.close()  # type: ignore[name-defined]
                except Exception:
                    pass

        t = _threading.Thread(target=_worker, daemon=True)
        t.start()
        t.join(timeout=timeout_s)
        try:
            self._probe_recent[port] = _time.monotonic()
        except Exception:
            pass
        return bool(result.get("ok"))

    def auto_connect_or_candidates(self) -> Tuple[Optional[str], List[Dict[str, Any]]]:
        """
        Try to connect automatically. Returns (connected_port, candidates).
        If connected_port is None and candidates is non-empty, the caller should show a pick list.
        If both are None/empty, log last_error.
        Flow:
          1) If explicit_port provided -> try it first
          2) Else if last_good_port exists -> try it
          3) Enumerate candidates with scores
          4) If exactly one high-confidence candidate -> try connect
          5) Else probe top candidates; if one verified -> connect
          6) Else return all viable candidates for user selection
        """
        self._last_error = None

        # If already connected
        if self._dc is not None:
            ident = self._dc.identity(silent=True)
            return ident.get("port") or self._explicit_port, []

        # 1) explicit port
        if self._explicit_port:
            p = self.try_connect(self._explicit_port)
            if p:
                return p, []
            # Soft warning; continue to scan
            log.warning("Failed to open explicit port %s; falling back to detection.", self._explicit_port)

        # 2) last good port
        if self._last_good_port:
            p = self.try_connect(self._last_good_port)
            if p:
                return p, []
            # Friendly message when saved device is not present
            log.warning("Couldn't find saved device on %s; searching for a Meshtastic device.", self._last_good_port)

        # 3) enumerate
        try:
            cands = self.detect_candidates()
        except Exception as e:
            self._last_error = {"code": "open_failed", "detail": str(e)}
            return None, []

        # No candidates
        if len(cands) == 0:
            self._last_error = {"code": "no_candidates", "detail": "No serial devices found"}
            return None, []

        # Filter: if we have any non-bluetooth candidates, deprioritize bluetooth-only ones
        non_bt = [c for c in cands if not c.get("is_bluetooth")]
        if non_bt:
            cands_use = non_bt
        else:
            cands_use = cands

        # 4) single high-confidence candidate
        top = cands_use[0]
        if len(cands_use) == 1 and int(top.get("score", 0)) >= 40:
            p = self.try_connect(top["path"]) 
            if p:
                return p, []

        # 5) probe up to top 3
        verified: List[Dict[str, Any]] = []
        for cand in cands_use[:3]:
            try:
                if self._probe_port(cand["path"], timeout_s=4.0):
                    v = dict(cand)
                    v["verified"] = True
                    verified.append(v)
            except Exception:
                continue
        if len(verified) == 1:
            p = self.try_connect(verified[0]["path"])
            if p:
                return p, []

        # 6) return candidates for user selection
        return None, cands_use

    # ---------------- Connect ----------------

    def connect_autodetect_if_single(self) -> Optional[str]:
        """
        Back-compat shim: try the advanced flow and only auto-connect on a single clear candidate.
        Returns connected port or None and sets last_error for UI.
        """
        port, candidates = self.auto_connect_or_candidates()
        if port:
            return port
        # Translate to legacy-style errors so older UI code remains coherent
        if not candidates:
            # last_error already set (no candidates / open_failed)
            return None
        # Multiple candidates case
        self._last_error = {"code": "multiple_candidates", "detail": "Multiple serial devices found", "candidates": candidates}
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

    # ---------------- Persistence helpers ----------------
    def _get_cache_path(self) -> str:
        try:
            base = os.path.expanduser("~")
            return os.path.join(base, ".mesh_configurator.json")
        except Exception:
            return ".mesh_configurator.json"

    def _load_last_good_port(self) -> Optional[str]:
        try:
            import json as _json
            if not os.path.isfile(self._cache_path):
                return None
            with open(self._cache_path, "r", encoding="utf-8") as f:
                data = _json.load(f) or {}
            return data.get("last_good_port")
        except Exception:
            return None

    def _save_last_good_port(self, port: str) -> None:
        try:
            import json as _json
            data: Dict[str, Any] = {"last_good_port": port}
            with open(self._cache_path, "w", encoding="utf-8") as f:
                _json.dump(data, f)
        except Exception:
            pass
