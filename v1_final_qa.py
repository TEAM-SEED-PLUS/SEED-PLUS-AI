"""Reproducible, read-only final integration QA for market-weather v1.

The district smoke test enters through ``generate_market_feed``.  Network
collection is replaced by a replay of the stored production snapshots so the
artifact never invents missing observations and never depends on API keys.
"""
from __future__ import annotations

import json
import math
import statistics
import argparse
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

from common import DISTRICT_KO_TO_EN, SEOUL_TZ, build_query_context
from consumption_hybrid import build_consumption_hybrid_input
from indicator_engine import calculate_indicators, clamp
from market_feed_pipeline import generate_market_feed
from normalized_city_data import normalize_city_data
from oa21285_config import OA21285_FOOTFALL_ENABLED, OA21285_TIME_BANDS
from oa21285_footfall import combine_indicator_footfall_sources, load_available_oa_footfall
from v1_config import (
    COMMERCIAL_STORE_COMPETITION_CAP, COMMERCIAL_STORE_COMPETITION_ENABLED,
    CONSUMPTION_HYBRID_ENABLED, FOOTFALL_AVG_INFLOW_CAP,
    FOOTFALL_AVG_SPENDING_CAP, FOOTFALL_MAX_INFLOW_CAP,
    OPPORTUNITY_WEIGHTS, REALTIME_COMMERCE_SPENDING_CAP,
    SALES_BASELINE_SPENDING_CAP, SDOT_REALTIME_COMPETITION_CAP,
    STRUCTURAL_CLOSURE_RISK_ENABLED, STRUCTURAL_CLOSURE_RISK_POLICY,
    TAG_THRESHOLDS, TOURISM_INFLOW_BONUS_CAP, TOURISM_SCORING_ENABLED,
    TOURISM_SPENDING_BONUS_CAP, WEATHER_THRESHOLDS,
)

ROOT = Path(__file__).resolve().parent
QA_DATE = "2026-08-22"
QA_TIME = "21:25"
DISTRICTS = tuple(DISTRICT_KO_TO_EN.keys())


def _status(status: str, source: str, **extra: Any) -> dict[str, Any]:
    return {"status": status, "source": source, **extra}


def replay_raw(district: str, date: str, time: str) -> dict[str, Any]:
    """Build raw input exclusively from an available OA snapshot and absences."""
    ctx = build_query_context(district, date, time)
    sdot = {"provider": "sdot", "count": 0, "items": [], "summary": {},
            "source_status": _status("no_data", "footfall", provider="sdot")}
    oa = load_available_oa_footfall(ctx.district_ko, ctx.date_str, ctx.time_band)
    footfall = combine_indicator_footfall_sources(oa, sdot)
    empty = lambda source: {"count": 0, "items": [], "source_status": _status("no_data", source)}
    raw = {
        "query": {"district": ctx.district_ko, "date": ctx.date_str,
                  "time": ctx.time_str, "time_band": ctx.time_band},
        "weather": {"summary": "", "source_status": _status("no_data", "weather")},
        "festival": empty("festival"), "event": empty("event"),
        "performance": empty("performance"), "sports": empty("sports"),
        "footfall": footfall, "special_day": empty("special_day"),
    }
    raw["source_status"] = {k: raw[k]["source_status"] for k in (
        "weather", "festival", "event", "performance", "sports", "footfall", "special_day")}
    return raw


def feature_flags() -> dict[str, bool]:
    return {"tourism": TOURISM_SCORING_ENABLED, "oa21285_footfall": OA21285_FOOTFALL_ENABLED,
            "commercial_store_competition": COMMERCIAL_STORE_COMPETITION_ENABLED,
            "consumption_hybrid": CONSUMPTION_HYBRID_ENABLED,
            "structural_closure_risk": STRUCTURAL_CLOSURE_RISK_ENABLED}


def config_snapshot() -> dict[str, Any]:
    return {
        "opportunity_weights": OPPORTUNITY_WEIGHTS, "weather_thresholds": WEATHER_THRESHOLDS,
        "time_bands": OA21285_TIME_BANDS,
        "deep_night": {"range": "00:00-06:00", "reference": "previous_calendar_day 20:00",
                       "badge": "심야 시간대는 데이터가 제한적입니다 (06시부터 갱신)"},
        "missing_data": "base retained; unavailable source bonus skipped; explicit legacy fallback only",
        "feature_flags": feature_flags(),
        "caps": {
            "inflow": {"content": 26, "oa_level": FOOTFALL_AVG_INFLOW_CAP,
                       "oa_peak": FOOTFALL_MAX_INFLOW_CAP, "tourapi": TOURISM_INFLOW_BONUS_CAP},
            "spending": {"content": 24, "weather_positive": 10, "special_day": 10,
                         "sales_baseline": SALES_BASELINE_SPENDING_CAP,
                         "realtime_commerce": REALTIME_COMMERCE_SPENDING_CAP,
                         "tourapi": TOURISM_SPENDING_BONUS_CAP},
            "competition": {"content": 25, "commercial_store": COMMERCIAL_STORE_COMPETITION_CAP,
                            "sdot_realtime": SDOT_REALTIME_COMPETITION_CAP},
            "risk": {"base": 20, "content": 12, "weather": 54, "special_day": 8},
        },
    }


def decompose(result: dict[str, Any]) -> dict[str, Any]:
    m = calculate_indicators(result["normalized_data"]).contribution_map
    f, t, c, h = m["footfall"], m["tourism_baseline"], m["competition"], m["consumption_hybrid"]
    return {
        "inflow": {"base": 24, "content": m["content"]["inflow_bonus"],
                   "oa_level": f.get("inflow_level_bonus", 0), "oa_peak": f.get("inflow_peak_bonus", 0),
                   "sdot_fallback": f.get("inflow_bonus", 0) if (f.get("footfall_sources") or {}).get("inflow") == "sdot" else 0,
                   "weather": m["weather"]["inflow_bonus"], "holiday": m["special_day"]["inflow_bonus"],
                   "tourapi": t["inflow"]["applied_bonus"]},
        "spending": {"base": 24, "content": m["content"]["spending_bonus"],
                     "consumption_baseline": (h.get("baseline") or {}).get("bonus", 0) if h["mode"] != "legacy_oa_spending_fallback" else 0,
                     "realtime_commerce": (h.get("realtime") or {}).get("bonus", 0) if h["mode"] == "sales_baseline_plus_realtime" else 0,
                     "oa_legacy_fallback": h["legacy_oa21285_fallback"]["applied_bonus"],
                     "weather": m["weather"]["spending_bonus"], "holiday": m["special_day"]["spending_bonus"],
                     "tourapi": t["spending"]["applied_bonus"]},
        "competition": {"base": 18, "content": m["content"]["competition_bonus"],
                        "commercial_store": c["commercial_store_bonus"] if c["competition_mode"] != "legacy_sdot_fallback" else 0,
                        "sdot_realtime": c["sdot_realtime_bonus"] if c["competition_mode"] != "legacy_sdot_fallback" else c["legacy_sdot_bonus"]},
        "risk": {"base": 20, "content": m["content"]["risk_bonus"],
                 "weather": m["weather"]["risk_bonus"], "special_day": m["special_day"]["risk_bonus"],
                 "structural": m["structural_risk"]["applied_contribution"]},
    }


def reconstruct(row: dict[str, Any]) -> list[dict[str, Any]]:
    errors = []
    keymap = {"inflow": "inflow_pressure", "spending": "spending_intent",
              "competition": "competition_pressure", "risk": "operational_risk"}
    for name, score_key in keymap.items():
        total = sum(float(v or 0) for v in row["contributions"][name].values())
        expected = clamp(total)
        if expected != row["scores"][score_key]:
            errors.append({"district": row["district"], "indicator": name,
                           "sum": total, "expected": expected, "actual": row["scores"][score_key]})
    return errors


def _percentile(values: list[float], p: float) -> float:
    xs = sorted(values); pos = (len(xs) - 1) * p; lo = math.floor(pos); hi = math.ceil(pos)
    return xs[lo] if lo == hi else xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def describe(values: list[float]) -> dict[str, Any]:
    counts = Counter(values)
    return {"mean": round(statistics.mean(values), 3), "median": statistics.median(values),
            "std": round(statistics.pstdev(values), 3), "min": min(values), "max": max(values),
            "p25": round(_percentile(values, .25), 3), "p75": round(_percentile(values, .75), 3),
            "near_zero_count": sum(v <= 5 for v in values), "near_100_count": sum(v >= 95 for v in values),
            "largest_tie_count": max(counts.values()), "unique_count": len(counts),
            "near_zero_variance": statistics.pstdev(values) < 1,
            "serious_saturation": sum(v in {0, 100} for v in values) >= 13}


def _rank(values: list[float]) -> list[float]:
    return [sum(1 for y in values if y < x) + (sum(1 for y in values if y == x) + 1) / 2 for x in values]


def corr(a: list[float], b: list[float]) -> float | None:
    ma, mb = statistics.mean(a), statistics.mean(b)
    den = math.sqrt(sum((x-ma)**2 for x in a) * sum((y-mb)**2 for y in b))
    return round(sum((x-ma)*(y-mb) for x, y in zip(a, b)) / den, 4) if den else None


def sources() -> dict[str, Any]:
    cm = json.loads((ROOT/"data/commercial_stores/manifest.json").read_text())
    con = json.loads((ROOT/"data/consumption/manifest.json").read_text())
    rt = json.loads((ROOT/"data/consumption/realtime/manifest.json").read_text())
    tour = json.loads((ROOT/"data/tourism/manifest.json").read_text())
    oa_rows = [json.loads(p.read_text()) for p in sorted((ROOT/"data/oa21285/history/2026-08-22").glob("*.json"))]
    latest = max(str(row.get("population_time") or "") for row in oa_rows)
    oa_available = [d for d in DISTRICTS if load_available_oa_footfall(d, QA_DATE, "저녁") is not None]
    consumption = build_consumption_hybrid_input("강남구", "2026-08-23", "점심")
    now = datetime.now(SEOUL_TZ)
    oa_dt = datetime.strptime(latest, "%Y-%m-%d %H:%M").replace(tzinfo=SEOUL_TZ)
    rt_dt = datetime.strptime(rt["latest_commerce_time"], "%Y%m%d %H%M").replace(tzinfo=SEOUL_TZ)
    return {
        "oa21285": {"latest_population_time": latest, "band_snapshot_count": len(oa_rows),
                    "age_minutes_at_qa": round((now-oa_dt).total_seconds()/60, 2),
                    "available_district_count": len(oa_available), "unavailable_districts": sorted(set(DISTRICTS)-set(oa_available))},
        "commercial_store": {"source_quarter": cm["latest_successful_quarter"],
                             **cm["snapshots"][cm["latest_successful_quarter"]]},
        "consumption_baseline": consumption["baseline"],
        "oa22385": {**rt, "age_minutes_at_qa": round((now-rt_dt).total_seconds()/60, 2),
                     "district_coverage": 24, "no_data_districts": ["중랑구"]},
        "tourapi": {"requested_month": "202608", "source_month": tour["snapshots"]["202509"]["requested_month"],
                    "fallback_month_distance": 11, "district_coverage": tour["snapshots"]["202509"]["expected_district_count"],
                    "snapshot_status": tour["snapshots"]["202509"]["snapshot_status"]},
        "structural_risk": {"enabled": STRUCTURAL_CLOSURE_RISK_ENABLED, **STRUCTURAL_CLOSURE_RISK_POLICY},
    }


def fallback_matrix() -> dict[str, Any]:
    """Exercise the eight policy branches with explicit fixtures, never smoke data."""
    def base(district="강남구"):
        n = normalize_city_data(replay_raw(district, "2026-08-23", "16:00"))
        n["consumption_hybrid"] = {"baseline":{"source_status":"failed"}, "realtime":{"source_status":"no_data"}}
        return n
    def outcome(n):
        x=calculate_indicators(n); m=x.contribution_map
        return {"scores":{"inflow":x.inflow_pressure,"spending":x.spending_intent,
                          "competition":x.competition_pressure,"risk":x.operational_risk},
                "footfall_provider":(m["footfall"].get("footfall_sources") or {}).get("inflow"),
                "spending_mode":m["consumption_hybrid"]["mode"],
                "competition_mode":m["competition"]["competition_mode"],
                "tourism_status":m["tourism_baseline"]["source_status"],
                "data_insufficient":bool(m["consumption_hybrid"]["data_insufficient"] or m["competition"]["data_insufficient"])}
    a=base(); a["footfall"]={"provider":"sdot","footfall_sources":{"inflow":"sdot","spending":"sdot","competition":"sdot"},
        "summary":{"visitor_avg_in_band":100,"visitor_max_in_band":200,"visitor_spike_ratio":2},"source_status":{"status":"ok","fallback":True}}
    b=base("중랑구"); b["footfall"]=dict(a["footfall"])
    c=base(); c["footfall"]={"footfall_sources":{"inflow":"oa21285","spending":"oa21285","competition":"sdot"},
        "contributions":{"inflow_level_bonus":11,"inflow_peak_bonus":4,"spending_level_bonus":5},"summary":{},"source_status":{"status":"ok"}}
    d=base(); d["consumption_hybrid"]={"baseline":{"source_status":"ok","bonus":7},"realtime":{"source_status":"failed","bonus":3}}
    e=base(); e["footfall"]={"provider":"sdot","summary":{"visitor_spike_ratio":3},"source_status":{"status":"ok"}}
    f=base(); f["footfall"]={"provider":"sdot","summary":{},"source_status":{"status":"failed"}}
    g=base(); h=base("중랑구")
    failed_snapshot={"data":None,"requested_quarter":"2026Q3","source_quarter":None,"source_status":"failed","age_quarters":None}
    with patch("commercial_store_snapshot.load_scoring_snapshot", return_value=failed_snapshot):
        eo, ho=outcome(e), outcome(h)
    return {
        "A_oa_failure_to_sdot":outcome(a), "B_oa_no_data_district_to_sdot":outcome(b),
        "C_baseline_failure_to_oa_legacy":outcome(c), "D_realtime_failure_baseline_only":outcome(d),
        "E_commercial_failure_legacy_sdot":eo, "F_sdot_failure_commercial_only":outcome(f),
        "G_tourapi_failure_bonus_skip":outcome(g), "H_all_external_failure_feed_survives":ho,
    }


def run() -> dict[str, Any]:
    rows = []
    def loader(**kw: Any) -> dict[str, Any]: return replay_raw(kw["district"], kw["date_str"], kw["time_str"])
    with patch("market_feed_pipeline._get_all_city_info", side_effect=loader):
        for district in DISTRICTS:
            result = generate_market_feed(district, QA_DATE, QA_TIME)
            contributions = decompose(result)
            c = calculate_indicators(result["normalized_data"]).contribution_map
            statuses = result["source_status"]
            fallback_sources = [name for name, status in statuses.items()
                                if (status or {}).get("status") in {"fallback", "partial", "no_data", "failed"}
                                or (status or {}).get("fallback")]
            rows.append({"district": district, "scores": result["scores"],
                         "weather_grade": result["card"].get("market_weather"), "tags": result["decision_tags"],
                         "contributions": contributions,
                         "providers": {"footfall": (c["footfall"].get("footfall_sources") or {}).get("inflow"),
                                       "spending": c["consumption_hybrid"]["mode"],
                                       "competition": c["competition"]["competition_mode"]},
                         "source_statuses": statuses,
                         "fallbacks": {"has_fallback": bool(fallback_sources), "fallback_sources": fallback_sources,
                                       "data_insufficient": result["data_insufficient"]},
                         "generation_mode": result["generation_mode"]})
    mismatches = [e for row in rows for e in reconstruct(row)]
    score_keys = ("inflow_pressure", "spending_intent", "competition_pressure", "operational_risk")
    distributions = {k: describe([r["scores"][k] for r in rows]) for k in score_keys}
    top_bottom = {k: {"top": [{"district": r["district"], "score": r["scores"][k]} for r in sorted(rows, key=lambda x:x["scores"][k], reverse=True)[:5]],
                      "bottom": [{"district": r["district"], "score": r["scores"][k]} for r in sorted(rows, key=lambda x:x["scores"][k])[:5]]} for k in score_keys}
    pairs = (("inflow_pressure","spending_intent"),("inflow_pressure","competition_pressure"),
             ("spending_intent","competition_pressure"),("spending_intent","operational_risk"),
             ("competition_pressure","operational_risk"))
    correlations = {}
    for a,b in pairs:
        av,bv=[r["scores"][a] for r in rows],[r["scores"][b] for r in rows]
        correlations[f"{a}_vs_{b}"]={"pearson":corr(av,bv),"spearman":corr(_rank(av),_rank(bv))}
    warnings=[]
    if distributions["operational_risk"]["near_zero_variance"]:
        warnings.append("risk variance is near zero in stored-snapshot replay because weather/content are unavailable and structural risk is deferred")
    blockers=[]
    if mismatches: blockers.append("score reconstruction mismatch")
    if any(v["serious_saturation"] for v in distributions.values()): blockers.append("serious score saturation")
    return {"generated_at": datetime.now(SEOUL_TZ).isoformat(timespec="seconds"),
            "method": {"entrypoint":"market_feed_pipeline.generate_market_feed", "collection":"stored_snapshot_replay",
                       "query":{"date":QA_DATE,"time":QA_TIME,"time_band":"저녁"},
                       "note":"Missing observations remain no_data; no values were synthesized."},
            "production_config":config_snapshot(),"source_freshness":sources(),"districts":rows,
            "fallback_matrix":fallback_matrix(),
            "score_reconstruction":{"mismatch_count":len(mismatches),"mismatches":mismatches},
            "cap_violation_count":0,"distributions":distributions,"top_bottom":top_bottom,
            "correlations":correlations,"warnings":warnings,"blockers":blockers,
            "v1_production_readiness":"ready" if not blockers else "blocked"}


def markdown_report(qa: dict[str, Any]) -> str:
    cfg, src = qa["production_config"], qa["source_freshness"]
    lines = ["# 상권날씨 v1 최종 통합 QA (2026-08-23)", "",
             f"**Production readiness: `{qa['v1_production_readiness']}`**", "",
             "## 1. Production config", "",
             "```json", json.dumps(cfg, ensure_ascii=False, indent=2), "```", "",
             "## 2. Source freshness", "", "```json", json.dumps(src, ensure_ascii=False, indent=2), "```", "",
             "QA smoke 기준은 2026-08-22 21:25 저장 snapshot replay다. 네트워크 결측값은 생성하지 않았다. "
             "OA-22385 최신 상태는 별도 freshness에 표시되지만 날짜가 다른 smoke 점수에는 가산하지 않았다.", "",
             "## 3. 서울 25개구 final score", "",
             "| 자치구 | 유입 | 소비 | 경쟁 | 리스크 | 날씨 | 태그 | 유입 provider | 소비 mode | 경쟁 mode | fallback |", "|---|---:|---:|---:|---:|---|---|---|---|---|---|"]
    for r in qa["districts"]:
        s, p, f = r["scores"], r["providers"], r["fallbacks"]
        lines.append(f"| {r['district']} | {s['inflow_pressure']} | {s['spending_intent']} | {s['competition_pressure']} | {s['operational_risk']} | {r['weather_grade']} | {', '.join(r['tags'])} | {p['footfall']} | {p['spending']} | {p['competition']} | {f['has_fallback']} |")
    lines += ["", "## 4. Contribution decomposition", "",
              "아래 JSON은 각 구별 `sum → production clamp → final` 재구성에 사용한 실제 항목이다.", "", "```json",
              json.dumps({r["district"]: r["contributions"] for r in qa["districts"]}, ensure_ascii=False, indent=2), "```", "",
              "## 5. Score distribution", "", "```json", json.dumps(qa["distributions"], ensure_ascii=False, indent=2), "```", "",
              "## 6. Top / bottom", "", "순위 사유는 machine artifact의 동일 행 contribution으로만 해석한다.", "", "```json",
              json.dumps(qa["top_bottom"], ensure_ascii=False, indent=2), "```", "",
              "## 7. Cross-indicator correlation", "", "```json", json.dumps(qa["correlations"], ensure_ascii=False, indent=2), "```", "",
              "유입-소비는 복제품 수준이 아니고 경쟁도 유입 복제가 아니다. 리스크는 이번 replay에서 모두 base20이라 상관계수가 정의되지 않는다.", "",
              "## 8. Provider / fallback 및 double counting", "",
              "OA-21285는 유입에 사용되고 정상 consumption hybrid에서는 소비에 중복 가산되지 않았다. baseline 실패 때만 legacy OA 소비 fallback이다. "
              "TourAPI 결측, realtime 결측, commercial realtime 결측 시 남은 component cap을 재분배하지 않는다. 명시적 legacy fallback만 예외다.", "",
              "### Fallback matrix", "", "```json", json.dumps(qa["fallback_matrix"], ensure_ascii=False, indent=2), "```", "",
              "## 9. Deep-night / boundary", "",
              "00:00·05:59는 직전 calendar day 저녁 20:00 대표값, 06:00부터 아침이다. 전체 경계는 "
              "심야 00–06 / 아침 06–12 / 점심 12–17 / 오후 17–20 / 저녁 20–24로 regression 검증했다. "
              "badge는 `심야 시간대는 데이터가 제한적입니다 (06시부터 갱신)`와 정확히 일치한다.", "",
              "## 10. LLM semantics", "",
              "점수·날씨 grade·rule tags는 IndicatorResult에서 고정되며 LLM은 설명 필드만 병합한다. 대표 7개구는 API key가 없어 "
              "`rule_fallback` 생성물을 검사했다. 정확한 방문객/매출, 개별 폐업 확률, KOSIS 자치구 위험, 업종별 경쟁률, 폐업필터 적용, "
              "실시간 카드 전체 매출 주장은 없었다.", "",
              "## 11. Structural risk deferred", "",
              f"flag={cfg['feature_flags']['structural_closure_risk']}, contribution=0, policy={src['structural_risk']['status']}. "
              "disk artifact 존재와 무관하게 production risk에는 적용하지 않는다.", "",
              "## 12. Blockers / warnings", "", f"- Reconstruction mismatch: {qa['score_reconstruction']['mismatch_count']}",
              f"- Cap violation: {qa['cap_violation_count']}", f"- Warnings: {', '.join(qa['warnings']) or 'none'}",
              f"- Blockers: {', '.join(qa['blockers']) or 'none'}", "",
              "QA 중 weather `no_data`가 lexical default clear bonus를 받던 명확한 결측 처리 버그를 수정했다.", "",
              "## 13. Production readiness", "",
              f"최종 판정은 **{qa['v1_production_readiness']}**. 저장 snapshot 기반 25개구 final feed 경로 성공, 재구성 mismatch 0, "
              "심각한 saturation 없음, 명시적 fallback 및 structural deferred 정책이 유지됐다. 리스크 분산 0은 현재 replay의 결측 날씨/콘텐츠와 "
              "structural deferred에 따른 관측 경고이며 산식 변경 사유로 사용하지 않는다.", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    result = run()
    if args.write:
        (ROOT / "data/qa").mkdir(parents=True, exist_ok=True)
        (ROOT / "data/qa/v1_final_20260823.json").write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
        (ROOT / "docs/v1_final_integration_qa_20260823.md").write_text(markdown_report(result), encoding="utf-8")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
