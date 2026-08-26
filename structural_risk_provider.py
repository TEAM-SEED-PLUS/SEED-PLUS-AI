"""Provider boundary for a future maintained structural-risk source.

Historical OA artifacts are queryable for diagnostics only.  Production has no
provider until every activation requirement is explicitly revalidated.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol


class StructuralRiskProvider(Protocol):
    def get_snapshot(self, period: str) -> dict[str, Any] | None: ...
    def get_district_closure_rate(self, district: str, period: str) -> float | None: ...
    def get_source_metadata(self) -> dict[str, Any]: ...


ACTIVATION_REQUIREMENTS = (
    "maintained_official_source",
    "seoul_25_districts_or_validated_lower_spatial_grain",
    "closed_and_total_store_counts_or_official_closure_rate",
    "confirmed_quarterly_or_yearly_refresh",
    "semantic_consistency_with_historical_metric",
    "complete_25_district_coverage",
    "full_regression_pass",
)


class HistoricalOAClosureProvider:
    """Read-only diagnostics adapter; never a production scoring provider."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root) if root else Path(__file__).resolve().parent / "data" / "risk" / "seoul_closure"

    def get_snapshot(self, period: str) -> dict[str, Any] | None:
        candidates = (self.root / "pooled" / f"{period}.json", self.root / "quarterly" / f"{period}.json")
        for path in candidates:
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            return value if isinstance(value, dict) else None
        return None

    def get_district_closure_rate(self, district: str, period: str) -> float | None:
        snapshot = self.get_snapshot(period) or {}
        row = (snapshot.get("districts") or {}).get(district) or {}
        value = row.get("pooled_closure_rate", row.get("closure_rate"))
        return float(value) if isinstance(value, (int, float)) else None

    def get_source_metadata(self) -> dict[str, Any]:
        return {"provider_mode": "research_historical_only", "service_active": False,
                "production_applied": False, "production_eligible": False,
                "semantics": "과거 서울시 점포 폐업률 기반 구조위험 참고값"}


def production_structural_risk_provider() -> StructuralRiskProvider | None:
    """Explicitly disabled; snapshot presence must never activate production."""
    return None
