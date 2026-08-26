"""Official OA-22172 archive/live validation for structural-risk research only."""

from __future__ import annotations

import csv
import io
import json
import math
import statistics
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

import requests

from commercial_store_snapshot import SEOUL_DISTRICT_CODES, percentile_ranks


DISTRICT_NAMES = {code: name for name, code in SEOUL_DISTRICT_CODES.items()}
KOREAN_FIELDS = {
    "기준_년분기_코드": "STDR_YYQU_CD", "행정동_코드": "ADSTRD_CD",
    "행정동_코드_명": "ADSTRD_CD_NM", "서비스_업종_코드": "SVC_INDUTY_CD",
    "서비스_업종_코드_명": "SVC_INDUTY_CD_NM", "점포_수": "STOR_CO",
    "유사_업종_점포_수": "SIMILR_INDUTY_STOR_CO", "개업_율": "OPBIZ_RT",
    "개업_점포_수": "OPBIZ_STOR_CO", "폐업_률": "CLSBIZ_RT",
    "폐업_점포_수": "CLSBIZ_STOR_CO", "프랜차이즈_점포_수": "FRC_STOR_CO",
}
NUMERIC_FIELDS = {"STOR_CO", "SIMILR_INDUTY_STOR_CO", "OPBIZ_RT", "OPBIZ_STOR_CO",
                  "CLSBIZ_RT", "CLSBIZ_STOR_CO", "FRC_STOR_CO"}


def api_to_quarter(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) == 5 and text[:4].isdigit() and text[4] in "1234":
        return f"{text[:4]}Q{text[4]}"
    raise ValueError(f"invalid quarter: {text}")


def _normalized_row(row: dict[str, Any]) -> dict[str, Any]:
    result = {KOREAN_FIELDS.get(key, key): value for key, value in row.items()}
    for field in NUMERIC_FIELDS:
        try:
            result[field] = float(result[field]) if result.get(field) not in (None, "") else None
        except (TypeError, ValueError):
            result[field] = None
    dong_code = str(result.get("ADSTRD_CD") or "").strip()
    district_code = dong_code[:5] if len(dong_code) >= 5 else ""
    result["SIGNGU_CD"] = district_code
    result["SIGNGU_CD_NM"] = DISTRICT_NAMES.get(district_code, "")
    return result


def parse_oa22172_zip(path: str | Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if len(names) != 1:
            raise ValueError("OA-22172 ZIP must contain exactly one CSV")
        content = archive.read(names[0]).decode("cp949")
    reader = csv.DictReader(io.StringIO(content))
    actual_fields = list(reader.fieldnames or [])
    missing = sorted(set(KOREAN_FIELDS) - set(actual_fields))
    if missing:
        raise ValueError(f"missing fields: {','.join(missing)}")
    rows = [_normalized_row(row) for row in reader]
    return rows, {"encoding": "cp949", "csv_name": names[0], "actual_fields": actual_fields,
                  "normalized_fields": [KOREAN_FIELDS[name] for name in actual_fields], "row_count": len(rows)}


def fetch_oa22172_quarter(api_key: str, quarter: str, destination: str | Path, *,
                          session: requests.Session | None = None, page_size: int = 1000,
                          timeout: int = 30) -> dict[str, Any]:
    api_period = quarter.replace("Q", "")
    if api_to_quarter(api_period) != quarter:
        raise ValueError("quarter must be YYYYQn")
    client = session or requests.Session()
    rows: list[dict[str, Any]] = []
    total = None
    start = 1
    calls = 0
    while total is None or start <= total:
        end = start + page_size - 1
        url = f"http://openapi.seoul.go.kr:8088/{api_key}/json/VwsmAdstrdStorW/{start}/{end}/{api_period}"
        try:
            response = client.get(url, timeout=timeout)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise RuntimeError(f"OA-22172 transport failure: {type(exc).__name__}") from exc
        payload = response.json()
        block = payload.get("VwsmAdstrdStorW") or {}
        result = block.get("RESULT") or payload.get("RESULT") or {}
        if str(result.get("CODE") or "") != "INFO-000":
            raise RuntimeError(f"OA-22172 API failure: {result.get('CODE') or 'unknown'}")
        total = int(block.get("list_total_count") or 0)
        rows.extend(_normalized_row(row) for row in (block.get("row") or []) if isinstance(row, dict))
        calls += 1
        start += page_size
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    temporary.replace(path)
    return {"source_dataset": "OA-22172", "official_period": quarter,
            "source_status": "historical_official", "service_active": False,
            "legacy_endpoint_probe_succeeded": True, "row_count": len(rows), "api_call_count": calls}


def aggregate_dong_rows(rows: Iterable[dict[str, Any]], quarter: str) -> dict[str, Any]:
    target = quarter.replace("Q", "")
    selected = [row for row in rows if str(row.get("STDR_YYQU_CD") or "") == target]
    seen: set[tuple[str, str, str]] = set()
    duplicate_count = unknown_district_count = 0
    districts: dict[str, dict[str, Any]] = {}
    dong_names: dict[str, set[str]] = defaultdict(set)
    industry_names: dict[str, set[str]] = defaultdict(set)
    for row in selected:
        dong = str(row.get("ADSTRD_CD") or "")
        industry = str(row.get("SVC_INDUTY_CD") or "")
        grain = (target, dong, industry)
        if grain in seen:
            duplicate_count += 1
            continue
        seen.add(grain)
        dong_names[dong].add(str(row.get("ADSTRD_CD_NM") or ""))
        industry_names[industry].add(str(row.get("SVC_INDUTY_CD_NM") or ""))
        code = str(row.get("SIGNGU_CD") or dong[:5])
        name = str(row.get("SIGNGU_CD_NM") or DISTRICT_NAMES.get(code) or "")
        if not name:
            unknown_district_count += 1
            continue
        block = districts.setdefault(name, {"district": name, "district_code": code,
            "total_store_count": 0.0, "closed_store_count": 0.0, "row_count": 0,
            "dong_codes": set(), "source_status": "ok"})
        total, closed = row.get("SIMILR_INDUTY_STOR_CO"), row.get("CLSBIZ_STOR_CO")
        if not isinstance(total, (int, float)) or not isinstance(closed, (int, float)):
            block["source_status"] = "partial"
            continue
        block["total_store_count"] += total
        block["closed_store_count"] += closed
        block["row_count"] += 1
        block["dong_codes"].add(dong)
    for block in districts.values():
        block["dong_count"] = len(block.pop("dong_codes"))
        total = block["total_store_count"]
        block["closure_rate"] = block["closed_store_count"] / total * 100 if total > 0 else None
        if total <= 0:
            block["source_status"] = "no_data"
    return {"quarter": quarter, "district_count": len(districts), "row_count": len(seen),
            "status": "complete" if len(districts) == 25 and not unknown_district_count else "partial",
            "diagnostics": {"duplicate_grain_count": duplicate_count,
                "unknown_district_count": unknown_district_count,
                "dong_code_multiple_names_count": sum(len(v) > 1 for v in dong_names.values()),
                "industry_code_multiple_names_count": sum(len(v) > 1 for v in industry_names.values())},
            "districts": dict(sorted(districts.items()))}


def compare_district_sources(dong: dict[str, Any], district: dict[str, Any], *,
                             rate_tolerance_pp: float = 0.01) -> dict[str, Any]:
    comparisons = {}
    near = 0
    for name in sorted(set(dong.get("districts", {})) | set(district.get("districts", {}))):
        left, right = (dong.get("districts", {}).get(name) or {}), (district.get("districts", {}).get(name) or {})
        total_diff = (left.get("total_store_count") or 0) - (right.get("total_store_count") or 0)
        closed_diff = (left.get("closed_store_count") or 0) - (right.get("closed_store_count") or 0)
        rate_diff = abs((left.get("closure_rate") or 0) - (right.get("closure_rate") or 0))
        if total_diff == 0 and closed_diff == 0 and rate_diff <= rate_tolerance_pp:
            near += 1
        comparisons[name] = {"total_store_absolute_difference": abs(total_diff),
            "total_store_relative_difference": abs(total_diff) / right["total_store_count"] if right.get("total_store_count") else None,
            "closed_store_absolute_difference": abs(closed_diff),
            "rate_difference_pp": rate_diff}
    rates = [row["rate_difference_pp"] for row in comparisons.values()]
    return {"district_count": len(comparisons), "exact_near_match_count": near,
            "mean_rate_difference_pp": statistics.fmean(rates) if rates else None,
            "max_rate_difference_pp": max(rates) if rates else None, "districts": comparisons}


def pooled_quarters(quarterly: dict[str, dict[str, Any]]) -> dict[str, Any]:
    complete = {q: value for q, value in quarterly.items() if value.get("status") == "complete"}
    districts = {}
    for name in SEOUL_DISTRICT_CODES:
        values = [(q, value["districts"].get(name)) for q, value in sorted(complete.items())]
        values = [(q, row) for q, row in values if row and row.get("source_status") == "ok"]
        total = sum(row["total_store_count"] for _, row in values)
        closed = sum(row["closed_store_count"] for _, row in values)
        districts[name] = {"district": name, "available_quarters": [q for q, _ in values],
            "pooled_total_store_count": total, "pooled_closed_store_count": closed,
            "pooled_closure_rate": closed / total * 100 if total else None}
    percentiles = percentile_ranks({name: row["pooled_closure_rate"] for name, row in districts.items()})
    for name, value in percentiles.items():
        districts[name]["pooled_percentile"] = value
    expected = ["2025Q2", "2025Q3", "2025Q4", "2026Q1"]
    return {"quarters": sorted(complete), "expected_quarters": expected,
            "status": "complete" if sorted(complete) == expected else "partial", "districts": districts}


def spearman(values_a: dict[str, float], values_b: dict[str, float]) -> float | None:
    keys = sorted(set(values_a) & set(values_b))
    if len(keys) < 2:
        return None
    ranks_a = percentile_ranks({key: values_a[key] for key in keys})
    ranks_b = percentile_ranks({key: values_b[key] for key in keys})
    a, b = [ranks_a[key] for key in keys], [ranks_b[key] for key in keys]
    mean_a, mean_b = statistics.fmean(a), statistics.fmean(b)
    numerator = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b))
    denominator = math.sqrt(sum((x - mean_a) ** 2 for x in a) * sum((y - mean_b) ** 2 for y in b))
    return numerator / denominator if denominator else None


def quarterly_stability(quarterly: dict[str, dict[str, Any]], pooled: dict[str, Any]) -> dict[str, Any]:
    quarters = sorted(quarterly)
    quarter_percentiles = {
        q: percentile_ranks({d: row.get("closure_rate") for d, row in value["districts"].items()})
        for q, value in quarterly.items()
    }
    districts = {}
    for district in SEOUL_DISTRICT_CODES:
        rates = {q: quarterly[q]["districts"][district]["closure_rate"] for q in quarters}
        pcts = {q: quarter_percentiles[q][district] for q in quarters}
        ranks = {q: 25 - round(pcts[q] / 100 * 24) for q in quarters}
        pooled_row = pooled["districts"][district]
        districts[district] = {"quarterly_closure_rates": rates, "quarterly_percentiles": pcts,
            "mean": statistics.fmean(rates.values()), "std": statistics.pstdev(rates.values()),
            "min": min(rates.values()), "max": max(rates.values()),
            "rank_min": min(ranks.values()), "rank_max": max(ranks.values()),
            "rank_range": max(ranks.values()) - min(ranks.values()),
            "pooled_closure_rate": pooled_row["pooled_closure_rate"],
            "pooled_percentile": pooled_row["pooled_percentile"]}
    adjacent = {}
    for left, right in zip(quarters, quarters[1:]):
        adjacent[f"{left}_{right}"] = spearman(quarter_percentiles[left], quarter_percentiles[right])
    latest = quarters[-1]
    pooled_pct = {d: row["pooled_percentile"] for d, row in pooled["districts"].items()}
    differences = {d: quarter_percentiles[latest][d] - pooled_pct[d] for d in SEOUL_DISTRICT_CODES}
    return {"quarters": quarters, "adjacent_spearman": adjacent,
        "latest_vs_pooled_spearman": spearman(quarter_percentiles[latest], pooled_pct),
        "largest_latest_pooled_differences": [
            {"district": district, "latest_percentile": quarter_percentiles[latest][district],
             "pooled_percentile": pooled_pct[district], "difference": differences[district]}
            for district in sorted(differences, key=lambda d: (-abs(differences[d]), d))[:5]],
        "districts": districts}


def structural_budget_preview(pooled: dict[str, Any], short_term: float = 23.0) -> dict[str, Any]:
    candidates = {"existing_base20": [], "candidate_a_structural_max20": [],
                  "candidate_b_fixed10_structural_max10": [], "candidate_c_fixed15_structural_max5": []}
    districts = {}
    for district, row in pooled["districts"].items():
        percentile = row["pooled_percentile"]
        values = {"existing_base20": short_term + 20,
            "candidate_a_structural_max20": short_term + percentile * 0.20,
            "candidate_b_fixed10_structural_max10": short_term + 10 + percentile * 0.10,
            "candidate_c_fixed15_structural_max5": short_term + 15 + percentile * 0.05}
        districts[district] = values
        for key, value in values.items():
            candidates[key].append(value)
    distribution = {key: {"mean": statistics.fmean(values), "median": statistics.median(values),
        "std": statistics.pstdev(values), "min": min(values), "max": max(values)}
        for key, values in candidates.items()}
    return {"fixed_short_term_contribution": short_term, "districts": districts,
            "distribution": distribution, "recommended_candidate": "candidate_b_fixed10_structural_max10"}
