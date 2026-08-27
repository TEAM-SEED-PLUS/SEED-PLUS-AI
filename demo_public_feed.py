"""기획팀 캡처용 Public Feed Schema v1 터미널 출력기.

최종 통합 QA와 동일한 저장 snapshot replay를 production feed pipeline에
연결한다. 점수, 태그, 문장 또는 결측값을 별도로 만들지 않는다.
"""
from __future__ import annotations

import argparse
from typing import Any
from unittest.mock import patch

from public_feed_schema import generate_public_market_feed
from v1_final_qa import QA_DATE, QA_TIME, replay_raw


def _display(value: Any) -> str:
    if value is None or value == "" or value == []:
        return "no_data"
    return str(value)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="상권날씨 v1 실제 Feed 결과 캡처")
    parser.add_argument("--district", required=True, help="서울 자치구명. 예: 강남구")
    parser.add_argument(
        "--date", default=QA_DATE, choices=[QA_DATE],
        help=f"검증 완료 snapshot 기준일 (현재 {QA_DATE}만 지원)",
    )
    parser.add_argument(
        "--time", default=QA_TIME, choices=[QA_TIME],
        help=f"검증 완료 snapshot 기준시각 (현재 {QA_TIME}만 지원)",
    )
    return parser


def _print_feed(feed: dict[str, Any]) -> None:
    query = feed.get("query") or {}
    weather = feed.get("market_weather") or {}
    indicators = feed.get("indicators") or {}
    narrative = feed.get("narrative") or {}
    quality = feed.get("data_quality") or {}
    tags = feed.get("decision_tags") or []
    actions = narrative.get("recommended_actions") or []
    generation_mode = _display(narrative.get("generation_mode"))

    print("=" * 60)
    print("상권날씨 v1 · 실제 Feed 결과")
    print("=" * 60)
    print()
    print(f"지역: {_display(query.get('district'))}")
    print(f"기준: {_display(query.get('date'))} {_display(query.get('time'))}")
    print(f"시간대: {_display(query.get('time_band'))}")
    print("Data mode: validated_snapshot")
    print()
    print(f"오늘의 상권날씨: {_display(weather.get('emoji'))} {_display(weather.get('grade'))}")
    print(f"기회점수: {_display(feed.get('opportunity_score'))}")
    print()
    print("[핵심 지표]")
    print(f"유입 압력      {_display(indicators.get('inflow_pressure'))}")
    print(f"소비 의도      {_display(indicators.get('spending_intent'))}")
    print(f"경쟁 압박      {_display(indicators.get('competition_pressure'))}")
    print(f"운영 리스크    {_display(indicators.get('operational_risk'))}")
    print()
    print("[의사결정 태그]")
    if tags:
        for tag in tags:
            print(f"- {_display(tag)}")
    else:
        print("no_data")
    print()
    print("[판단]")
    print(_display(narrative.get("judgement_sentence")))
    print()
    print("[근거]")
    print(_display(narrative.get("basis_sentence")))
    print()
    print("[추천 액션]")
    if actions:
        for number, action in enumerate(actions, start=1):
            print(f"{number}. {_display(action)}")
    else:
        print("no_data")
    print()
    print("[데이터 상태]")
    print(f"상태: {_display(quality.get('status'))}")
    sections = (
        ("실제 Fallback", "fallback_sources"),
        ("데이터 없음", "no_data_sources"),
        ("오래되어 미적용", "stale_sources"),
        ("시점 불일치로 미적용", "skipped_sources"),
        ("정상 조회 / 해당 항목 없음", "empty_sources"),
        ("실패", "failed_sources"),
    )
    for label, key in sections:
        values = quality.get(key) or []
        print(f"{label}:")
        if values:
            for value in values:
                print(f"- {value}")
        else:
            print("- 없음")
    print(f"데이터 부족: {_display(quality.get('data_insufficient'))}")
    print()
    print("[문장 생성 방식]")
    print(generation_mode)
    if generation_mode == "rule_fallback":
        print("(OpenAI API 키 없이 룰 기반으로 생성)")
    print()
    print("Data source: 최종 통합 QA에서 검증한 저장 snapshot replay")
    print("=" * 60)


def main() -> None:
    args = _build_parser().parse_args()

    def loader(**kwargs: Any) -> dict[str, Any]:
        return replay_raw(kwargs["district"], kwargs["date_str"], kwargs["time_str"])

    try:
        with patch("market_feed_pipeline._get_all_city_info", side_effect=loader):
            feed = generate_public_market_feed(args.district, args.date, args.time)
    except Exception:
        raise SystemExit("Feed 생성에 실패했습니다. API 키나 원문 응답은 출력하지 않습니다.")

    _print_feed(feed)


if __name__ == "__main__":
    main()
