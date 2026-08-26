"""OA-21285 공식 장소 목록의 버전 snapshot 관리."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable


DEFAULT_REFERENCE_DIR = Path(__file__).resolve().parent / "data" / "oa21285" / "reference"


def build_reference_snapshot(
    places: Iterable[dict[str, Any]], reference_date: str, *, source: str = "OA-21285 official place list"
) -> dict[str, Any]:
    unique: dict[str, dict[str, Any]] = {}
    for place in places:
        area_cd = str(place.get("AREA_CD") or place.get("area_cd") or "").strip()
        if not area_cd:
            continue
        unique[area_cd] = {
            "area_cd": area_cd,
            "area_nm": str(place.get("AREA_NM") or place.get("area_nm") or "").strip(),
            "category": str(place.get("CATEGORY") or place.get("category") or "").strip(),
            "eng_nm": str(place.get("ENG_NM") or place.get("eng_nm") or "").strip(),
            "reference_date": reference_date,
        }
    return {
        "reference_date": reference_date,
        "source": source,
        "place_count": len(unique),
        "places": [unique[code] for code in sorted(unique)],
    }


def save_reference_snapshot(snapshot: dict[str, Any], path: str | Path | None = None) -> Path:
    reference_date = str(snapshot.get("reference_date") or "unknown").replace("-", "")
    target = Path(path) if path else DEFAULT_REFERENCE_DIR / f"places_{reference_date}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)
    return target


def load_reference_snapshot(path: str | Path | None = None) -> dict[str, Any]:
    if path is None:
        candidates = sorted(DEFAULT_REFERENCE_DIR.glob("places_*.json"), reverse=True)
        if not candidates:
            raise FileNotFoundError("OA-21285 reference snapshot이 없습니다.")
        path = candidates[0]
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("places"), list):
        raise ValueError("OA-21285 reference snapshot 형식이 올바르지 않습니다.")
    return payload


def reference_by_code(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(place["area_cd"]): place for place in snapshot.get("places", []) if place.get("area_cd")}
