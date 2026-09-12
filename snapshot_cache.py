"""Small, atomic JSON snapshots shared by offline collectors and request loaders."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from common import SEOUL_TZ


def write_snapshot(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    return path


def read_snapshot(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def snapshot_age_minutes(payload: dict[str, Any], now: datetime | None = None) -> float | None:
    value = payload.get("generated_at") or payload.get("received_at")
    try:
        generated = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=SEOUL_TZ)
    current = now or datetime.now(SEOUL_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=SEOUL_TZ)
    return round(max(0.0, (current - generated).total_seconds() / 60), 2)
