from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional, Dict, Any

from .preset_controller import PresetController

log = logging.getLogger(__name__)


class AppState:
    """Tiny JSON-backed app state store.

    Stored alongside presets in ~/.meshtastic_config_presets/app_state.json
    Currently tracks: preferred_port (for auto-connect)
    """

    FILENAME = "app_state.json"

    @classmethod
    def _state_path(cls) -> Optional[Path]:
        pc = PresetController()
        if pc.preset_dir is None:
            return None
        return pc.preset_dir / cls.FILENAME

    @classmethod
    def load(cls) -> Dict[str, Any]:
        p = cls._state_path()
        if p is None or not p.exists():
            return {}
        try:
            return json.loads(p.read_text(encoding="utf-8")) or {}
        except Exception as e:
            log.warning("[app-state] failed to read %s: %s", p, e)
            return {}

    @classmethod
    def save(cls, data: Dict[str, Any]) -> bool:
        p = cls._state_path()
        if p is None:
            return False
        try:
            p.write_text(json.dumps(data or {}, indent=2), encoding="utf-8")
            return True
        except Exception as e:
            log.warning("[app-state] failed to write %s: %s", p, e)
            return False

    # Convenience helpers
    @classmethod
    def get_preferred_port(cls) -> Optional[str]:
        return (cls.load() or {}).get("preferred_port") or None

    @classmethod
    def set_preferred_port(cls, port: str) -> bool:
        d = cls.load() or {}
        d["preferred_port"] = port
        return cls.save(d)

    @classmethod
    def clear_preferred_port(cls) -> bool:
        d = cls.load() or {}
        if "preferred_port" in d:
            d.pop("preferred_port", None)
            return cls.save(d)
        return True
