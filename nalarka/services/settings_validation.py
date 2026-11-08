"""Whitelist + type validation for PUT /api/config."""
from pathlib import Path
from typing import Any, Dict, List, Tuple

from ..db.seed import DEFAULT_SETTINGS
from .paths import safe_output_name

ALLOWED: Dict[str, str] = {k: t for k, _v, t in DEFAULT_SETTINGS}
_TRUE = {"true", "1", "yes", "on"}
_FALSE = {"false", "0", "no", "off"}


def _coerce(vtype: str, v: Any) -> Any:
    if vtype == "int":
        if isinstance(v, bool):
            raise ValueError("expected integer")
        return int(float(v)) if isinstance(v, str) and v.strip() else int(v)
    if vtype == "float":
        if isinstance(v, bool):
            raise ValueError("expected number")
        return float(v)
    if vtype == "bool":
        if isinstance(v, bool):
            return v
        if isinstance(v, str) and v.strip().lower() in _TRUE | _FALSE:
            return v.strip().lower() in _TRUE
        raise ValueError("expected boolean")
    if not isinstance(v, str):
        raise ValueError("expected string")
    return v


def validate_settings(settings: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """Return (clean, errors). Unknown keys, wrong types and bad values become errors."""
    clean: Dict[str, Any] = {}
    errors: List[str] = []
    for k, v in settings.items():
        vtype = ALLOWED.get(k)
        if vtype is None:
            errors.append(f"{k}: unknown setting")
            continue
        try:
            val = _coerce(vtype, v)
            if k == "render.max_duration":
                val = max(15, min(900, val))
            elif k == "render.workers":
                val = max(1, min(16, val))
            elif k.startswith("tts.") and vtype == "float":
                val = max(0.0, min(1.0, val))
            elif k == "render.output_name":
                val = safe_output_name(val)
            elif k == "library.dir" and not Path(val).is_dir():
                raise ValueError("folder does not exist")
            clean[k] = val
        except (ValueError, TypeError, OverflowError) as e:
            errors.append(f"{k}: {e}")
    return clean, errors
