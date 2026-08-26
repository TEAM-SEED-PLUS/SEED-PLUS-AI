from __future__ import annotations

from typing import Any

try:
    from common import normalize_text
    from indicator_engine import IndicatorResult
    from v1_config import TAG_THRESHOLDS
except ImportError:  # pragma: no cover
    from .common import normalize_text
    from .indicator_engine import IndicatorResult
    from .v1_config import TAG_THRESHOLDS


TAG_PRIORITY = [
    "진입 유리",
    "기회 구간",
    "특정 업종만 유리",
    "관망 권장",
    "리스크 주의",
    "과열 상태",
]


def _pick_tags(tags: list[str], limit: int = 2) -> list[str]:
    """최종 카드에 보여줄 의사결정 태그 2개를 우선순위 기준으로 선택합니다."""
    ordered = [tag for tag in TAG_PRIORITY if tag in tags]
    ordered += [tag for tag in tags if tag not in ordered]
    return ordered[:limit] or ["관망 권장"]


def _story_lines(normalized_data: dict[str, Any], limit: int = 4) -> list[str]:
    """축제/행사/공연/스포츠에서 근거로 쓸 수 있는 문장만 추립니다."""
    lines: list[str] = []
    for key, label in [
        ("festival", "축제"),
        ("event", "행사"),
        ("performance", "공연"),
        ("sports", "스포츠"),
    ]:
        for line in normalized_data.get(key, {}).get("story_lines", [])[:1]:
            clean = normalize_text(line)
            if clean:
                lines.append(f"{label} 포착: {clean}")
    return lines[:limit]


def _judgement_sentence(indicators: IndicatorResult, score_context: dict[str, Any] | None = None) -> str:
    tags = indicators.decision_tags or []

    if (score_context or {}).get("basis") == "previous_evening_reference":
        return "현재 심야 실측 점수가 아니라, 직전 저녁 참고값을 기준으로 운영 판단에 활용해 주세요."

    if "진입 유리" in tags and "기회 구간" in tags:
        return "유입과 소비 전환 가능성이 동시에 높아, 오늘은 적극적인 노출과 현장 전환을 노려볼 만한 구간입니다."
    if "진입 유리" in tags:
        return "운영 리스크가 과도하지 않아, 현재 시간대에는 기본적인 영업 강화 전략을 적용하기 좋은 구간입니다."
    if "과열 상태" in tags:
        return "수요는 강하지만 경쟁과 혼잡 부담이 커, 무리한 할인보다 회전율 관리가 더 중요한 구간입니다."
    if "리스크 주의" in tags:
        return "날씨나 혼잡 변수가 있어 공격적인 집행보다는 재고·인력·동선 관리가 우선입니다."
    if "특정 업종만 유리" in tags:
        return "전체 상권이 고르게 좋은 상황이라기보다, 콘텐츠와 맞는 업종 중심으로 기회가 제한적으로 열려 있습니다."
    return "뚜렷한 상승 신호가 강하지 않아, 추가 집행보다는 관찰과 보수적 운영이 적합한 구간입니다."


def _basis_sentence(
    normalized_data: dict[str, Any],
    indicators: IndicatorResult,
    score_context: dict[str, Any] | None = None,
) -> str:
    evidence = indicators.evidence or {}
    is_reference = (score_context or {}).get("basis") == "previous_evening_reference"
    reason_summary = evidence.get("reason_summary", []) or []
    if reason_summary:
        sentence = normalize_text(reason_summary[0])
        if is_reference:
            return "직전 저녁 참고 데이터 기준으로 " + sentence
        return sentence

    story = _story_lines(normalized_data, limit=1)
    if story:
        return ("직전 저녁 참고 데이터 기준으로 " if is_reference else "") + story[0]

    sentence = (
        f"기회 점수 {indicators.opportunity_score}/100 기준으로 "
        f"유입 압력 {indicators.inflow_pressure}, 소비 의도 {indicators.spending_intent}, "
        f"경쟁 압박 {indicators.competition_pressure}, 운영 리스크 {indicators.operational_risk}를 종합했습니다."
    )
    return ("직전 저녁 참고 데이터 기준으로 " if is_reference else "") + sentence


def _first_content_hint(normalized_data: dict[str, Any]) -> str:
    for key in ["festival", "event", "performance", "sports"]:
        lines = normalized_data.get(key, {}).get("story_lines", []) or []
        if lines:
            return normalize_text(lines[0])
    return ""


def _weather_hint(normalized_data: dict[str, Any]) -> str:
    weather = normalized_data.get("weather", {}) or {}
    summary = normalize_text(weather.get("summary")) or "날씨 정보"
    temp = weather.get("temperature_c")
    rain = weather.get("precipitation_probability_percent")
    parts = [summary]
    if temp is not None:
        parts.append(f"{temp}°C")
    if rain is not None:
        parts.append(f"강수확률 {rain}%")
    return " / ".join(parts)


def _recommended_actions(normalized_data: dict[str, Any], indicators: IndicatorResult) -> list[str]:
    """LLM 실패 시 사용하는 fallback 액션도 지표·데이터 기반으로 최대한 구체화합니다."""
    tags = indicators.decision_tags or []
    categories = indicators.recommended_categories or []
    time_band = (normalized_data.get("query", {}) or {}).get("time_band") or "해당 시간대"
    content_hint = _first_content_hint(normalized_data)
    weather_hint = _weather_hint(normalized_data)
    category_text = ", ".join(categories[:2]) if categories else "대표 메뉴"
    actions: list[str] = []

    if "진입 유리" in tags or "기회 구간" in tags:
        actions.append(
            f"{time_band} 전후 유입 압력 {indicators.inflow_pressure}점을 활용해 입간판·SNS에 오늘 한정 메뉴를 먼저 노출하세요."
        )
    else:
        actions.append(
            f"유입 압력 {indicators.inflow_pressure}점 기준으로 대형 광고보다 단골·예약 고객 대상 저비용 알림을 우선하세요."
        )

    if categories:
        actions.append(
            f"소비 의도 {indicators.spending_intent}점에 맞춰 {category_text} 중심의 1~2인 세트나 포장 구성을 전면에 배치하세요."
        )
    elif content_hint:
        actions.append(
            f"'{content_hint}' 방문객이 바로 고를 수 있도록 빠른 주문 메뉴와 포장 옵션을 계산대 근처에 배치하세요."
        )
    else:
        actions.append(
            f"소비 의도 {indicators.spending_intent}점 기준으로 고가 세트보다 진입 장벽 낮은 소액 메뉴를 먼저 제안하세요."
        )

    if "리스크 주의" in tags or indicators.operational_risk >= TAG_THRESHOLDS["risk_caution"]:
        actions.append(
            f"운영 리스크 {indicators.operational_risk}점과 {weather_hint}를 반영해 실내 좌석·포장 동선·우천 재고를 먼저 점검하세요."
        )
    elif "과열 상태" in tags or indicators.competition_pressure >= TAG_THRESHOLDS["overheated_competition"]:
        actions.append(
            f"경쟁 압박 {indicators.competition_pressure}점이 높으므로 할인보다 대기줄 분리, 선결제, 빠른 회전 메뉴로 대응하세요."
        )
    elif content_hint:
        actions.append(
            f"'{content_hint}' 종료 전후 1시간에는 주문 인력 1명을 전면 배치하고 인기 품목 품절을 막으세요."
        )
    else:
        actions.append(
            f"운영 리스크 {indicators.operational_risk}점이 낮은 편이므로 재고는 평시보다 소폭만 늘리고 회전율을 확인하세요."
        )

    # 중복 제거 후 3개 고정
    out: list[str] = []
    for item in actions:
        if item not in out:
            out.append(item)
    return out[:3]


def build_market_feed_card(
    query: dict[str, Any],
    normalized_data: dict[str, Any],
    indicators: IndicatorResult,
) -> dict[str, Any]:
    """최종 피드 카드 1개만 생성합니다."""
    district = query.get("district") or normalized_data.get("query", {}).get("district") or "자치구"
    score_context = query.get("score_context") if isinstance(query.get("score_context"), dict) else None

    return {
        "district": district,
        "weather_icon": indicators.market_weather_emoji,
        "market_weather": indicators.market_weather,
        "decision_tags": _pick_tags(indicators.decision_tags),
        "judgement_sentence": _judgement_sentence(indicators, score_context),
        "basis_sentence": _basis_sentence(normalized_data, indicators, score_context),
        "indicators": {
            "유입 압력": indicators.inflow_pressure,
            "소비 의도": indicators.spending_intent,
            "경쟁 압박": indicators.competition_pressure,
            "운영 리스크": indicators.operational_risk,
        },
        "recommended_actions": _recommended_actions(normalized_data, indicators),
    }


def format_market_feed(card: dict[str, Any]) -> str:
    """서비스 화면에 그대로 넣을 수 있는 최종 피드 문자열입니다."""
    scores = card.get("indicators", {}) or {}
    tags = card.get("decision_tags", []) or ["관망 권장"]
    actions = card.get("recommended_actions", []) or []

    lines = [
        f"📍 {card.get('district', '자치구')}",
        "",
        f"{card.get('weather_icon', '')} 오늘의 상권 날씨 : {card.get('market_weather', '')}",
        *(["", str(card.get("notice"))] if card.get("notice") else []),
        "",
        "🏷️ " + " · ".join(tags[:2]),
        "",
        str(card.get("judgement_sentence", "")).strip(),
        str(card.get("basis_sentence", "")).strip(),
        "",
        f"📊 {card.get('score_label') or '핵심 지표'}",
        f"유입 압력 {scores.get('유입 압력', 0)}",
        f"소비 의도 {scores.get('소비 의도', 0)}",
        f"경쟁 압박 {scores.get('경쟁 압박', 0)}",
        f"운영 리스크 {scores.get('운영 리스크', 0)}",
        "",
        "💡 추천 액션",
        "",
    ]
    lines.extend(f"- {action}" for action in actions[:3])
    return "\n".join(lines).strip()
