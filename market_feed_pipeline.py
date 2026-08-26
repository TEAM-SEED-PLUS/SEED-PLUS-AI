from __future__ import annotations

import argparse
import json
from datetime import timedelta
from pathlib import Path
from typing import Any

try:
    from common import DEFAULT_KOPIS_KEY, DEFAULT_PUBLIC_DATA_KEY, DEFAULT_SEOUL_KEY, build_query_context
    from normalized_city_data import normalize_city_data
    from indicator_engine import calculate_indicators
    from feed_renderer import build_market_feed_card, format_market_feed
    from llm_feed_writer import LLMFeedError, generate_hybrid_market_feed_card
    from tourism_monthly import get_cached_district_tourism_baseline
    from consumption_hybrid import attach_consumption_hybrid
    from v1_config import NIGHT_NOTICE, REFERENCE_EVENING_TIME
except ImportError:  # pragma: no cover
    from .common import DEFAULT_KOPIS_KEY, DEFAULT_PUBLIC_DATA_KEY, DEFAULT_SEOUL_KEY, build_query_context
    from .normalized_city_data import normalize_city_data
    from .indicator_engine import calculate_indicators
    from .feed_renderer import build_market_feed_card, format_market_feed
    from .llm_feed_writer import LLMFeedError, generate_hybrid_market_feed_card
    from .tourism_monthly import get_cached_district_tourism_baseline
    from .consumption_hybrid import attach_consumption_hybrid
    from .v1_config import NIGHT_NOTICE, REFERENCE_EVENING_TIME


def _get_all_city_info(**kwargs: Any) -> dict[str, Any]:
    """선택 데이터소스 의존성은 실제 파이프라인 실행 시에만 불러옵니다."""
    try:
        from integrated_city_info import get_all_city_info
    except ImportError:  # pragma: no cover
        from .integrated_city_info import get_all_city_info
    return get_all_city_info(**kwargs)


def _attach_cached_tourism_baseline(normalized_data: dict[str, Any]) -> dict[str, Any]:
    """피드 요청 중 API 호출 없이 해당 월의 저장된 관광 snapshot만 연결한다."""
    query = normalized_data.get("query", {}) or {}
    date_value = str(query.get("date") or "").replace("-", "")
    base_ym = date_value[:6] if len(date_value) >= 6 else ""
    district = str(query.get("district") or "")
    baseline = None
    if base_ym and district:
        baseline = get_cached_district_tourism_baseline(district, base_ym)
    if baseline is None:
        baseline = {
            "requested_month": base_ym,
            "source_month": None,
            "source_status": "no_data",
            "snapshot_status": "no_data",
            "district": district,
            "metrics": {},
        }
    normalized_data["tourism_baseline"] = baseline
    return normalized_data


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="상권 날씨 피드 1개 생성 파이프라인")
    parser.add_argument("--district", required=True, help="서울 자치구명. 예: 송파구")
    parser.add_argument("--date", default=None, help="조회 날짜. 예: 2026-05-13 또는 20260513")
    parser.add_argument("--time", default=None, help="조회 시간. 예: 18:30 또는 1830")

    parser.add_argument("--public-key", default=DEFAULT_PUBLIC_DATA_KEY)
    parser.add_argument("--seoul-key", default=DEFAULT_SEOUL_KEY)
    parser.add_argument("--kopis-key", default=DEFAULT_KOPIS_KEY)

    parser.add_argument("--culture-limit", type=int, default=12)
    parser.add_argument("--sports-limit", type=int, default=10)
    parser.add_argument("--footfall-limit", type=int, default=10)

    parser.add_argument("--output-json", default=None, help="카드 데이터 JSON 저장 경로")
    parser.add_argument("--output-feed", default=None, help="최종 피드 텍스트 저장 경로")

    parser.add_argument("--llm-item-limit", type=int, default=5, help="LLM에 전달할 행사/공연/축제/스포츠 샘플 개수")

    parser.add_argument(
        "--show-data",
        action="store_true",
        help="최종 피드 아래에 피드 생성에 사용된 데이터 내용을 함께 출력합니다.",
    )
    parser.add_argument(
        "--data-sections",
        nargs="+",
        default=["all"],
        choices=["all", "weather", "special_day", "festival", "event", "performance", "sports", "footfall"],
        help="출력할 데이터 블록을 선택합니다. 예: --data-sections weather event special_day",
    )
    parser.add_argument(
        "--data-view",
        default="summary",
        choices=["summary", "json"],
        help="데이터 출력 형식입니다. summary는 사람이 보기 쉬운 요약, json은 선택 블록 원본 JSON입니다.",
    )
    parser.add_argument(
        "--data-source",
        default="normalized",
        choices=["normalized", "raw"],
        help="출력할 데이터 기준입니다. normalized는 정규화 데이터, raw는 API 수집 원천 데이터입니다.",
    )
    parser.add_argument(
        "--data-limit",
        type=int,
        default=5,
        help="각 데이터 블록에서 출력할 샘플 개수입니다.",
    )
    return parser


def generate_market_feed(
    district: str,
    date_str: str | None = None,
    time_str: str | None = None,
    public_key: str = DEFAULT_PUBLIC_DATA_KEY,
    seoul_key: str = DEFAULT_SEOUL_KEY,
    kopis_key: str = DEFAULT_KOPIS_KEY,
    culture_limit: int = 12,
    sports_limit: int = 10,
    footfall_limit: int = 10,
    llm_item_limit: int = 5,
    allow_rule_fallback: bool = True,
) -> dict[str, Any]:
    """
    상권 날씨 피드 1개를 생성합니다.

    흐름:
    1. 자치구/날짜/시간 정규화
    2. 날씨·특일·축제·행사·공연·스포츠·유동인구 수집
    3. 공통 스키마로 정규화
    4. 4대 지표 계산
    5. 룰베이스가 점수·날씨·태그를 확정하고, LLM은 판단/근거/액션 문장만 생성
       - LLM 실패 시 룰베이스 카드로 fallback
    """
    ctx = build_query_context(district, date_str, time_str)

    raw_data = _get_all_city_info(
        district=ctx.district_ko,
        date_str=ctx.date_str,
        time_str=ctx.time_str,
        public_key=public_key,
        seoul_key=seoul_key,
        kopis_key=kopis_key,
        culture_limit=culture_limit,
        sports_limit=sports_limit,
        footfall_limit=footfall_limit,
    )

    current_normalized_data = attach_consumption_hybrid(
        _attach_cached_tourism_baseline(normalize_city_data(raw_data)))
    scoring_raw_data = raw_data
    normalized_data = current_normalized_data
    score_context: dict[str, Any] = {"basis": "requested_time", "query": raw_data["query"]}
    reference = None
    notice = None

    if ctx.time_band == "심야":
        reference_date = (ctx.target_datetime - timedelta(days=1)).strftime("%Y-%m-%d")
        scoring_raw_data = _get_all_city_info(
            district=ctx.district_ko,
            date_str=reference_date,
            time_str=REFERENCE_EVENING_TIME,
            public_key=public_key,
            seoul_key=seoul_key,
            kopis_key=kopis_key,
            culture_limit=culture_limit,
            sports_limit=sports_limit,
            footfall_limit=footfall_limit,
        )
        normalized_data = attach_consumption_hybrid(
            _attach_cached_tourism_baseline(normalize_city_data(scoring_raw_data)))
        score_context = {
            "basis": "previous_evening_reference",
            "reference_date": reference_date,
            "reference_time_band": "저녁",
            "reference_start": "20:00",
            "reference_end": "24:00",
            "representative_time": REFERENCE_EVENING_TIME,
            "single_time_sources": {"weather": REFERENCE_EVENING_TIME},
            "query": scoring_raw_data["query"],
        }
        notice = NIGHT_NOTICE

    indicators = calculate_indicators(normalized_data)
    tourism_quality = (indicators.contribution_map.get("tourism_baseline", {}) or {}).get("source_status", "no_data")

    llm_error = None
    try:
        llm_query = dict(scoring_raw_data["query"])
        if notice:
            llm_query.update({
                "requested_query": raw_data["query"],
                "score_context": score_context,
                "notice": notice,
            })
        generated = generate_hybrid_market_feed_card(
            query=llm_query,
            normalized_data=normalized_data,
            indicators=indicators,
            item_limit=llm_item_limit,
            allow_rule_fallback=allow_rule_fallback,
            return_generation_mode=True,
        )
        card, generation_mode, llm_error = generated
    except LLMFeedError as exc:
        llm_error = str(exc)
        card = build_market_feed_card(raw_data["query"], normalized_data, indicators)
        generation_mode = "rule_fallback"

    if notice:
        card["notice"] = notice
        card["score_label"] = "직전 저녁 참고값"
        card["score_context"] = score_context

    feed_text = format_market_feed(card)

    scores = {
        "inflow_pressure": indicators.inflow_pressure,
        "spending_intent": indicators.spending_intent,
        "competition_pressure": indicators.competition_pressure,
        "operational_risk": indicators.operational_risk,
        "opportunity_score": indicators.opportunity_score,
    }
    if notice:
        reference = {
            "label": "직전 저녁 참고값",
            "query": scoring_raw_data["query"],
            "scores": scores,
            "source_status": scoring_raw_data.get("source_status", {}),
            "data_insufficient": normalized_data.get("data_insufficient", False),
        }

    response_source_status = dict(raw_data.get("source_status", {}))
    response_source_status["tourism_baseline"] = {
        "status": tourism_quality,
        "requested_month": (indicators.contribution_map.get("tourism_baseline", {}) or {}).get("requested_month"),
        "source_month": (indicators.contribution_map.get("tourism_baseline", {}) or {}).get("source_month"),
        "age_months": (indicators.contribution_map.get("tourism_baseline", {}) or {}).get("age_months"),
        "fallback_reason": (indicators.contribution_map.get("tourism_baseline", {}) or {}).get("fallback_reason"),
    }
    competition_quality = indicators.contribution_map.get("competition", {}) or {}
    response_source_status["commercial_store_competition"] = {
        "status": competition_quality.get("source_status"),
        "mode": competition_quality.get("competition_mode"),
        "requested_quarter": competition_quality.get("requested_quarter"),
        "source_quarter": competition_quality.get("source_quarter"),
        "age_quarters": competition_quality.get("age_quarters"),
        "snapshot_status": competition_quality.get("snapshot_status"),
        "closure_filter_applied": competition_quality.get("closure_filter_applied"),
    }
    consumption_quality = indicators.contribution_map.get("consumption_hybrid", {}) or {}
    response_source_status["consumption_hybrid"] = {
        "status": "fallback" if consumption_quality.get("mode") == "legacy_oa_spending_fallback" else
                  "partial" if consumption_quality.get("data_insufficient") else "ok",
        "mode": consumption_quality.get("mode"),
        "requested_quarter": (consumption_quality.get("baseline") or {}).get("requested_quarter"),
        "source_quarter": (consumption_quality.get("baseline") or {}).get("source_quarter"),
        "age_quarters": (consumption_quality.get("baseline") or {}).get("age_quarters"),
        "latest_commerce_time": (consumption_quality.get("realtime") or {}).get("commerce_time"),
        "age_minutes": (consumption_quality.get("realtime") or {}).get("age_minutes"),
        "valid_place_count": (consumption_quality.get("realtime") or {}).get("valid_place_count"),
    }

    return {
        "query": raw_data["query"],
        "card": card,
        "feed_text": feed_text,
        "raw_data": raw_data,
        "normalized_data": current_normalized_data,
        "scores": scores,
        "decision_tags": indicators.decision_tags,
        "evidence": indicators.evidence,
        "generation_mode": generation_mode,
        "llm_error": llm_error,
        "score_context": score_context,
        "reference": reference,
        "notice": notice,
        "source_status": response_source_status,
        "data_insufficient": (
            current_normalized_data.get("data_insufficient", False)
            or tourism_quality in {"partial", "no_data", "failed"}
            or bool(competition_quality.get("data_insufficient"))
            or bool(consumption_quality.get("data_insufficient"))
        ),
    }


DATA_SECTION_LABELS = {
    "weather": "날씨",
    "special_day": "공휴일/특일",
    "festival": "축제",
    "event": "행사",
    "performance": "공연",
    "sports": "스포츠",
    "footfall": "유동인구",
}


def print_feed(result: dict[str, Any]) -> str:
    return str(result.get("feed_text", "")).strip()


def _compact_item(item: dict[str, Any]) -> dict[str, Any]:
    """원천/정규화 데이터를 콘솔에서 확인하기 쉬운 필드만 남깁니다."""
    keys = [
        "title",
        "match",
        "category",
        "category_name",
        "genre",
        "place",
        "stadium",
        "time_text",
        "time",
        "start_date",
        "end_date",
        "date",
        "visitor_count",
        "administrative_district",
        "summary",
        "dateName",
        "isHoliday",
    ]
    compact = {k: item.get(k) for k in keys if item.get(k) not in (None, "", [], {})}
    return compact or item


def _print_block_summary(title: str, block: dict[str, Any], limit: int = 5) -> None:
    print("=" * 80)
    print(title)
    print("=" * 80)

    if not isinstance(block, dict):
        print(json.dumps(block, ensure_ascii=False, indent=2))
        return

    if title == "날씨":
        fields = [
            ("요약", block.get("summary")),
            ("기온", block.get("temperature_c")),
            ("습도", block.get("humidity_percent")),
            ("강수확률", block.get("precipitation_probability_percent")),
            ("강수량", block.get("precipitation_mm")),
            ("풍속", block.get("wind_speed_kmh")),
            ("예보시각", block.get("forecast_time")),
            ("출처", block.get("source")),
        ]
        for k, v in fields:
            if v not in (None, "", [], {}):
                print(f"- {k}: {v}")
        return

    if title == "유동인구":
        summary = block.get("summary", {}) or {}
        if summary:
            print("[요약]")
            for k, v in summary.items():
                if v not in (None, "", [], {}):
                    print(f"- {k}: {v}")
        items = block.get("items", []) or []
        if items:
            print("\n[샘플]")
            for idx, item in enumerate(items[:limit], start=1):
                print(f"[{idx}] " + json.dumps(_compact_item(item), ensure_ascii=False))
        else:
            print("데이터 없음")
        return

    count = block.get("count")
    if count is not None:
        print(f"건수: {count}")

    if title == "공휴일/특일":
        names = block.get("holiday_names") or []
        if names:
            print("공휴일/특일명: " + ", ".join(map(str, names)))

    items = block.get("items", []) or []
    if not items:
        print("데이터 없음")
        return

    for idx, item in enumerate(items[:limit], start=1):
        print(f"[{idx}] " + json.dumps(_compact_item(item), ensure_ascii=False))


def format_data_sections(
    result: dict[str, Any],
    sections: list[str],
    view: str = "summary",
    source: str = "normalized",
    limit: int = 5,
) -> str:
    """피드 생성에 사용된 데이터 블록을 선택적으로 출력합니다."""
    if "all" in sections:
        sections = list(DATA_SECTION_LABELS.keys())

    data_key = "raw_data" if source == "raw" else "normalized_data"
    data = result.get(data_key, {}) or {}

    if view == "json":
        selected = {sec: data.get(sec) for sec in sections if sec in DATA_SECTION_LABELS}
        return json.dumps(selected, ensure_ascii=False, indent=2)

    lines: list[str] = []
    # summary view는 print 함수 대신 문자열 생성을 위해 임시 캡처를 사용하지 않고 직접 조립합니다.
    for sec in sections:
        if sec not in DATA_SECTION_LABELS:
            continue
        title = DATA_SECTION_LABELS[sec]
        block = data.get(sec, {})
        lines.append("=" * 80)
        lines.append(title)
        lines.append("=" * 80)

        if sec == "weather" and isinstance(block, dict):
            fields = [
                ("요약", block.get("summary")),
                ("기온", block.get("temperature_c")),
                ("습도", block.get("humidity_percent")),
                ("강수확률", block.get("precipitation_probability_percent")),
                ("강수량", block.get("precipitation_mm")),
                ("풍속", block.get("wind_speed_kmh")),
                ("예보시각", block.get("forecast_time")),
                ("출처", block.get("source")),
            ]
            found = False
            for k, v in fields:
                if v not in (None, "", [], {}):
                    lines.append(f"- {k}: {v}")
                    found = True
            if not found:
                lines.append("데이터 없음")
            lines.append("")
            continue

        if sec == "footfall" and isinstance(block, dict):
            summary = block.get("summary", {}) or {}
            if summary:
                lines.append("[요약]")
                for k, v in summary.items():
                    if v not in (None, "", [], {}):
                        lines.append(f"- {k}: {v}")
            items = block.get("items", []) or []
            if items:
                lines.append("")
                lines.append("[샘플]")
                for idx, item in enumerate(items[:limit], start=1):
                    lines.append(f"[{idx}] " + json.dumps(_compact_item(item), ensure_ascii=False))
            elif not summary:
                lines.append("데이터 없음")
            lines.append("")
            continue

        if isinstance(block, dict):
            count = block.get("count")
            if count is not None:
                lines.append(f"건수: {count}")
            if sec == "special_day":
                names = block.get("holiday_names") or []
                if names:
                    lines.append("공휴일/특일명: " + ", ".join(map(str, names)))
            items = block.get("items", []) or []
            if items:
                for idx, item in enumerate(items[:limit], start=1):
                    lines.append(f"[{idx}] " + json.dumps(_compact_item(item), ensure_ascii=False))
            else:
                lines.append("데이터 없음")
        else:
            lines.append(json.dumps(block, ensure_ascii=False, indent=2))
        lines.append("")

    return "\n".join(lines).strip()


def main() -> None:
    args = build_parser().parse_args()

    result = generate_market_feed(
        district=args.district,
        date_str=args.date,
        time_str=args.time,
        public_key=args.public_key,
        seoul_key=args.seoul_key,
        kopis_key=args.kopis_key,
        culture_limit=args.culture_limit,
        sports_limit=args.sports_limit,
        footfall_limit=args.footfall_limit,
        llm_item_limit=args.llm_item_limit,
        allow_rule_fallback=True,
    )

    feed_text = print_feed(result)
    print(feed_text)

    if args.show_data:
        data_text = format_data_sections(
            result=result,
            sections=args.data_sections,
            view=args.data_view,
            source=args.data_source,
            limit=args.data_limit,
        )
        if data_text:
            print("\n\n" + data_text)

    if args.output_json:
        Path(args.output_json).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nJSON 저장 완료: {args.output_json}")

    if args.output_feed:
        Path(args.output_feed).write_text(feed_text, encoding="utf-8")
        print(f"피드 저장 완료: {args.output_feed}")


if __name__ == "__main__":
    main()
