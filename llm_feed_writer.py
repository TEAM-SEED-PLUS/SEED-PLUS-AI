from __future__ import annotations

import json
import os
import re
from typing import Any

try:
    from indicator_engine import IndicatorResult
    from feed_renderer import build_market_feed_card
    from v1_config import TAG_THRESHOLDS
except ImportError:  # pragma: no cover
    from .indicator_engine import IndicatorResult
    from .feed_renderer import build_market_feed_card
    from .v1_config import TAG_THRESHOLDS


# OpenAI is optional.  Values are environment-derived so production code,
# examples, and tests never need to contain a credential.
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

ALLOWED_DECISION_TAGS = [
    "진입 유리",
    "관망 권장",
    "리스크 주의",
    "특정 업종만 유리",
    "과열 상태",
    "기회 구간",
]

TAG_GUIDE = {
    "진입 유리": "유입 압력과 소비 의도가 충분하고, 운영 리스크와 경쟁 압박이 과도하지 않을 때",
    "관망 권장": "수요 신호가 약하거나 리스크/경쟁 압박이 커서 적극 집행이 애매할 때",
    "리스크 주의": "비, 폭우, 강풍, 야외 행사, 혼잡, 유동인구 급증 등 운영 부담이 클 때",
    "특정 업종만 유리": "공연, 스포츠, 가족 행사, 축제 등 콘텐츠 성격상 일부 업종만 수혜가 예상될 때",
    "과열 상태": "유입은 높지만 경기, 축제, 대형 행사 등으로 경쟁·혼잡·대기 관리 부담이 클 때",
    "기회 구간": "유입 압력과 소비 의도가 동시에 높고, 리스크가 낮아 매출 전환 가능성이 높을 때",
}


class LLMFeedError(RuntimeError):
    """LLM 기반 피드 생성에 실패했을 때 발생시키는 예외입니다."""


def _as_items(block: dict[str, Any], limit: int = 5) -> list[dict[str, Any]]:
    items = block.get("items", []) if isinstance(block, dict) else []
    if not isinstance(items, list):
        return []

    out: list[dict[str, Any]] = []
    for item in items[:limit]:
        if not isinstance(item, dict):
            continue
        out.append({
            "title": item.get("title") or item.get("match") or item.get("dateName") or item.get("summary"),
            "category": item.get("category") or item.get("genre") or item.get("sport") or item.get("source"),
            "place": item.get("place") or item.get("stadium") or item.get("address") or item.get("administrative_district"),
            "time_text": item.get("time_text") or item.get("time") or item.get("date"),
            "detail": item.get("detail") or item.get("runtime") or item.get("price_text"),
            "tags": item.get("tags"),
        })
    return out



def _brief_content_items(normalized_data: dict[str, Any], item_limit: int = 5) -> list[str]:
    """LLM 액션 생성을 위해 실제 콘텐츠명을 짧게 모읍니다."""
    briefs: list[str] = []
    for key, label in [
        ("festival", "축제"),
        ("event", "행사"),
        ("performance", "공연"),
        ("sports", "스포츠"),
    ]:
        block = normalized_data.get(key, {}) or {}
        for item in _as_items(block, item_limit):
            title = str(item.get("title") or "").strip()
            place = str(item.get("place") or "").strip()
            time_text = str(item.get("time_text") or "").strip()
            parts = [x for x in [title, place, time_text] if x]
            if parts:
                briefs.append(f"{label}: " + " / ".join(parts))
    return briefs[:item_limit]


def _build_action_hints(normalized_data: dict[str, Any], indicators: IndicatorResult, item_limit: int = 5) -> dict[str, Any]:
    """LLM이 두루뭉술한 액션 대신 점수·데이터 기반 실행안을 만들도록 힌트를 제공합니다."""
    weather = normalized_data.get("weather", {}) or {}
    footfall = (normalized_data.get("footfall", {}) or {}).get("summary", {}) or {}
    special_day = normalized_data.get("special_day", {}) or {}
    content_briefs = _brief_content_items(normalized_data, item_limit)

    hints: list[str] = []
    if indicators.inflow_pressure >= TAG_THRESHOLDS["inflow_opportunity"]:
        hints.append("유입 압력이 높으므로 매장 앞 노출, 지도/SNS 공지, 피크타임 전 준비 액션을 포함")
    elif indicators.inflow_pressure <= 45:
        hints.append("유입 압력이 낮으므로 대형 집행보다 기존 고객 재방문, 예약/포장, 저비용 노출 액션을 포함")

    if indicators.spending_intent >= TAG_THRESHOLDS["spending_opportunity"]:
        hints.append("소비 의도가 높으므로 객단가 상승 세트, 행사 연계 메뉴, 빠른 구매 전환 액션을 포함")
    elif indicators.spending_intent <= 45:
        hints.append("소비 의도가 낮으므로 할인 남발보다 체류 유도, 소액 메뉴, 재고 보수 운영 액션을 포함")

    if indicators.competition_pressure >= TAG_THRESHOLDS["overheated_competition"]:
        hints.append("경쟁 압박이 높으므로 가격 할인 경쟁보다 대기 관리, 회전율, 차별 메뉴/시간대 분산 액션을 포함")

    if indicators.operational_risk >= TAG_THRESHOLDS["risk_caution"]:
        hints.append("운영 리스크가 높으므로 우천/혼잡/강풍 대응, 인력 배치, 재고 축소/안전 동선 액션을 포함")

    weather_summary = str(weather.get("summary") or "")
    rain_prob = weather.get("precipitation_probability_percent")
    if "비" in weather_summary or "눈" in weather_summary or (isinstance(rain_prob, (int, float)) and rain_prob >= 50):
        hints.append("비·눈 가능성이 있으므로 실내 좌석, 포장/배달, 우산 보관, 미끄럼 방지 등 날씨 대응 액션을 포함")

    holiday_names = special_day.get("holiday_names") or []
    if holiday_names:
        hints.append(f"공휴일/특일({', '.join(map(str, holiday_names[:2]))}) 효과를 반영한 가족·외출 고객 대응 액션을 포함")

    if content_briefs:
        hints.append("아래 실제 콘텐츠명 중 최소 1개를 액션에 반영")

    return {
        "action_quality_rules": [
            "각 액션은 25~55자 정도의 한 문장으로 작성",
            "가능하면 시간대, 대상 고객, 업종/메뉴, 운영 방식 중 2개 이상 포함",
            "'노출을 강화하세요', '피크 대응하세요'처럼 추상적인 표현만 쓰지 말 것",
            "없는 행사명·장소명·수치를 새로 만들지 말 것",
            "세 액션의 역할을 각각 다르게 구성: ①마케팅/노출 ②상품·재고 ③운영·인력/동선",
        ],
        "score_based_hints": hints,
        "actual_content_briefs": content_briefs,
        "recommended_categories_from_scores": indicators.recommended_categories,
        "footfall_summary": footfall,
    }

def build_llm_context(
    query: dict[str, Any],
    normalized_data: dict[str, Any],
    indicators: IndicatorResult,
    item_limit: int = 5,
) -> dict[str, Any]:
    """LLM이 태그/판단/근거/액션을 생성할 때 참고할 최소 데이터만 구성합니다."""
    weather = normalized_data.get("weather", {}) or {}
    special_day = normalized_data.get("special_day", {}) or {}
    footfall = normalized_data.get("footfall", {}) or {}
    overview = normalized_data.get("overview", {}) or {}
    tourism = (indicators.contribution_map or {}).get("tourism_baseline", {}) or {}
    competition = (indicators.contribution_map or {}).get("competition", {}) or {}

    return {
        "query": {
            "district": query.get("district"),
            "date": query.get("date"),
            "time": query.get("time"),
            "time_band": query.get("time_band"),
            "requested_query": query.get("requested_query"),
            "score_context": query.get("score_context"),
            "notice": query.get("notice"),
        },
        "allowed_decision_tags": ALLOWED_DECISION_TAGS,
        "tag_selection_guide": TAG_GUIDE,
        "rule_candidate_tags": indicators.decision_tags,
        "rule_reason_summary": (indicators.evidence or {}).get("reason_summary", []),
        "scores": {
            "유입 압력": indicators.inflow_pressure,
            "소비 의도": indicators.spending_intent,
            "경쟁 압박": indicators.competition_pressure,
            "운영 리스크": indicators.operational_risk,
            "기회 점수": indicators.opportunity_score,
        },
        "fixed_output_fields": {
            "market_weather": indicators.market_weather,
            "weather_icon": indicators.market_weather_emoji,
        },
        "tourism_baseline": {
            "data_semantics": "월간 자치구 상대지표이며 실시간 관광객 수나 실제 당일 매출이 아님",
            "requested_month": tourism.get("requested_month"),
            "source_month": tourism.get("source_month"),
            "age_months": tourism.get("age_months"),
            "snapshot_status": tourism.get("snapshot_status"),
            "source_status": tourism.get("source_status"),
            "fallback_reason": tourism.get("fallback_reason"),
            "scoring_enabled": tourism.get("scoring_enabled"),
            "tourism_inflow_index": (tourism.get("inflow") or {}).get("index"),
            "tourism_spending_index": (tourism.get("spending") or {}).get("index"),
            "inflow_components": (tourism.get("inflow") or {}).get("components", {}),
            "spending_components": (tourism.get("spending") or {}).get("components", {}),
        },
        "competition": {
            "data_semantics": (
                "자치구 전체 점포 수를 면적으로 나눈 밀도의 서울 25개구 상대 Percentile. "
                "동일 업종 경쟁률·폐업률이 아니며 업종별 competition과 국세청 폐업 필터는 사용하지 않음"
            ),
            **competition,
        },
        "weather": {
            "summary": weather.get("summary"),
            "temperature_c": weather.get("temperature_c"),
            "humidity_percent": weather.get("humidity_percent"),
            "precipitation_probability_percent": weather.get("precipitation_probability_percent"),
            "precipitation_mm": weather.get("precipitation_mm"),
            "wind_speed_kmh": weather.get("wind_speed_kmh"),
            "forecast_time": weather.get("forecast_time"),
            "source": weather.get("source"),
        },
        "special_day": {
            "holiday_count": special_day.get("holiday_count"),
            "holiday_names": special_day.get("holiday_names", []),
            "items": _as_items(special_day, item_limit),
        },
        "footfall": {
            "footfall_sources": footfall.get("footfall_sources", {
                "inflow": footfall.get("provider", "sdot"),
                "spending": footfall.get("provider", "sdot"),
                "competition": "sdot",
            }),
            "data_semantics": footfall.get("data_semantics"),
            "summary": footfall.get("summary", {}),
            "source_status": footfall.get("source_status", {}),
        },
        "content": {
            "festival": {
                "count": normalized_data.get("festival", {}).get("count", 0),
                "story_lines": normalized_data.get("festival", {}).get("story_lines", [])[:item_limit],
                "items": _as_items(normalized_data.get("festival", {}), item_limit),
            },
            "event": {
                "count": normalized_data.get("event", {}).get("count", 0),
                "story_lines": normalized_data.get("event", {}).get("story_lines", [])[:item_limit],
                "items": _as_items(normalized_data.get("event", {}), item_limit),
            },
            "performance": {
                "count": normalized_data.get("performance", {}).get("count", 0),
                "story_lines": normalized_data.get("performance", {}).get("story_lines", [])[:item_limit],
                "items": _as_items(normalized_data.get("performance", {}), item_limit),
            },
            "sports": {
                "count": normalized_data.get("sports", {}).get("count", 0),
                "story_lines": normalized_data.get("sports", {}).get("story_lines", [])[:item_limit],
                "items": _as_items(normalized_data.get("sports", {}), item_limit),
            },
            "overview": {
                "top_content_titles": overview.get("top_content_titles", [])[:item_limit],
                "content_tag_counts": overview.get("content_tag_counts", {}),
                "story_digest": overview.get("llm_story_digest", [])[:item_limit],
            },
        },
        "engine_reference": {
            "recommended_categories_from_scores": indicators.recommended_categories,
            "avoid_reasons_from_scores": indicators.avoid_reasons,
            "signal_flags_from_scores": indicators.signal_flags,
        },
        "action_planning_context": _build_action_hints(normalized_data, indicators, item_limit),
    }


def _extract_json(text: str) -> dict[str, Any]:
    s = text.strip()
    s = re.sub(r"^```(?:json)?", "", s).strip()
    s = re.sub(r"```$", "", s).strip()
    match = re.search(r"\{.*\}", s, flags=re.DOTALL)
    if match:
        s = match.group(0)
    try:
        data = json.loads(s)
    except json.JSONDecodeError as exc:
        raise LLMFeedError(f"LLM 응답 JSON 파싱 실패: {exc}\n응답 일부: {text[:500]}") from exc
    if not isinstance(data, dict):
        raise LLMFeedError("LLM 응답은 JSON object여야 합니다.")
    return data


def _dedupe_tags(tags: list[str]) -> list[str]:
    out: list[str] = []
    for tag in tags:
        tag = str(tag).strip()
        if tag in ALLOWED_DECISION_TAGS and tag not in out:
            out.append(tag)
    return out


def _top_rule_tags(rule_candidate_tags: list[str]) -> list[str]:
    cleaned = _dedupe_tags(rule_candidate_tags)
    return cleaned[:2] or ["관망 권장"]


def validate_llm_tags(
    llm_tags: Any,
    rule_candidate_tags: list[str],
    indicators: IndicatorResult,
) -> list[str]:
    """
    LLM 태그를 후처리합니다.

    원칙:
    1. 허용 태그 6개만 사용합니다.
    2. 최종 태그 2개 중 최소 1개는 룰베이스 후보에서 가져옵니다.
    3. 점수와 명백히 모순되는 태그는 보정합니다.
    4. LLM 결과가 불안정하면 룰베이스 후보로 fallback합니다.
    """
    # v1: LLM은 태그를 선택하거나 보정하지 않는다. 룰 결과만 화면 개수에 맞춰 사용한다.
    return _top_rule_tags(rule_candidate_tags)


def _validate_text_fields(card: dict[str, Any]) -> tuple[str, str, list[str]]:
    judgement = str(card.get("judgement_sentence") or "").strip()
    basis = str(card.get("basis_sentence") or "").strip()
    actions = card.get("recommended_actions")

    if not judgement or not basis:
        raise LLMFeedError("judgement_sentence와 basis_sentence는 비어 있으면 안 됩니다.")

    if not isinstance(actions, list):
        raise LLMFeedError("recommended_actions는 list여야 합니다.")
    actions = [str(a).strip().lstrip("- ").strip() for a in actions if str(a).strip()]
    if len(actions) < 3:
        raise LLMFeedError("recommended_actions는 3개 이상 필요합니다.")

    # 너무 추상적인 액션은 LLM 결과 품질이 낮은 것으로 보고 fallback/재시도를 유도합니다.
    vague_patterns = [
        "노출을 강화", "홍보를 강화", "피크 대응", "데이터를 관찰", "상황을 확인",
        "운영을 조정", "준비하세요", "관리하세요",
    ]
    concrete_actions: list[str] = []
    for action in actions:
        # 길이가 너무 짧거나 추상 표현 하나로 끝나면 제외
        if len(action) < 18:
            continue
        if any(p == action.replace(".", "").strip() for p in vague_patterns):
            continue
        concrete_actions.append(action)

    if len(concrete_actions) < 3:
        # 완전히 실패시키면 allow_rule_fallback=True일 때 룰 기반 구체 액션으로 대체됩니다.
        raise LLMFeedError("recommended_actions가 너무 추상적입니다.")
    return judgement, basis, concrete_actions[:3]


def _merge_llm_with_rule_safety(
    raw_card: dict[str, Any],
    query: dict[str, Any],
    normalized_data: dict[str, Any],
    indicators: IndicatorResult,
) -> dict[str, Any]:
    """LLM 결과를 사용하되, 태그와 고정 수치는 룰베이스로 검증합니다."""
    fallback_district = query.get("district") or normalized_data.get("query", {}).get("district") or "자치구"
    tags = _top_rule_tags(indicators.decision_tags)
    judgement, basis, actions = _validate_text_fields(raw_card)
    score_context = query.get("score_context") if isinstance(query.get("score_context"), dict) else {}
    if score_context.get("basis") == "previous_evening_reference":
        misleading = ("현재 상권", "현재 심야", "심야 점수", "현재 점수")
        if any(text in judgement or text in basis for text in misleading):
            raise LLMFeedError("심야 참고값을 현재 실측값으로 표현한 LLM 문장입니다.")

    return {
        "district": str(raw_card.get("district") or fallback_district),
        "weather_icon": indicators.market_weather_emoji,
        "market_weather": indicators.market_weather,
        "decision_tags": tags,
        "judgement_sentence": judgement,
        "basis_sentence": basis,
        "indicators": {
            "유입 압력": indicators.inflow_pressure,
            "소비 의도": indicators.spending_intent,
            "경쟁 압박": indicators.competition_pressure,
            "운영 리스크": indicators.operational_risk,
        },
        "recommended_actions": actions,
    }


def generate_hybrid_market_feed_card(
    query: dict[str, Any],
    normalized_data: dict[str, Any],
    indicators: IndicatorResult,
    item_limit: int = 5,
    allow_rule_fallback: bool = True,
    return_generation_mode: bool = False,
) -> dict[str, Any] | tuple[dict[str, Any], str, str | None]:
    """
    룰베이스 + LLM 하이브리드 피드 생성.

    룰베이스:
    - 점수 계산
    - 태그 후보 생성
    - 위험한 모순 방지
    - fallback 담당

    LLM:
    - 실제 데이터명을 근거로 판단 문장 생성
    - 상인 입장에서 실행 가능한 액션 3개 생성
    """
    api_key = os.getenv("OPENAI_API_KEY") or OPENAI_API_KEY
    model = os.getenv("OPENAI_MODEL") or OPENAI_MODEL
    if not api_key:
        if allow_rule_fallback:
            card = build_market_feed_card(query, normalized_data, indicators)
            if return_generation_mode:
                return card, "rule_fallback", "OPENAI_API_KEY가 없습니다."
            return card
        raise LLMFeedError("OPENAI_API_KEY 환경변수가 없습니다.")

    try:
        from openai import OpenAI
    except ImportError as exc:
        if allow_rule_fallback:
            card = build_market_feed_card(query, normalized_data, indicators)
            if return_generation_mode:
                return card, "rule_fallback", "openai 패키지가 설치되어 있지 않습니다."
            return card
        raise LLMFeedError("openai 패키지가 설치되어 있지 않습니다. `pip install openai` 후 다시 실행하세요.") from exc

    llm_context = build_llm_context(query, normalized_data, indicators, item_limit=item_limit)
    district = query.get("district") or normalized_data.get("query", {}).get("district") or "자치구"

    system_prompt = """
너는 서울 상권 데이터를 기반으로 소상공인용 '상권 날씨 피드'를 작성하는 한국어 데이터 해석가다.
반드시 제공된 데이터와 점수만 근거로 사용하고, 확인되지 않은 행사/장소/수치를 지어내지 않는다.
룰베이스 후보 태그는 안전장치이므로 반드시 존중한다.
TourAPI 관광 데이터는 월간 자치구 상대지표이며 실시간 관광객 수나 실제 당일 매출이 아니다.
관광 데이터를 현재 관광객, 지금 관광 소비, 오늘 외국인 방문으로 표현하지 않는다.
상가 competition은 자치구 전체 점포 밀도의 서울 25개구 상대 수준이다.
이를 동일 업종 경쟁률, 정확한 경쟁 점포 수, 폐업률로 표현하지 않는다.
업종별 competition이나 국세청 폐업 필터를 사용했다고 표현하지 않는다.
출력은 JSON object 하나만 반환한다.
""".strip()

    user_prompt = f"""
아래 데이터를 참고해 최종 카드 피드의 문장 요소를 생성하라.

[룰베이스 후보 태그]
{indicators.decision_tags}

[문장 생성 원칙]
1. judgement_sentence는 조회 맥락에 맞는 상권 판단 문장 1개를 한국어 존댓말로 쓴다. 심야 reference이면 직전 저녁 참고 판단으로 쓴다.
2. basis_sentence는 날씨/행사/공휴일/유동인구/지표 중 실제 제공된 근거를 사용해 1문장으로 쓴다.
3. basis_sentence에는 가능하면 실제 데이터명, 행사명, 경기명, 공휴일명, 날씨 수치 중 1개 이상을 포함한다.
4. recommended_actions는 소상공인/점포 운영자가 바로 적용할 수 있는 액션 문장 정확히 3개를 쓴다.
5. 액션은 반드시 데이터와 지표를 반영해 구체적으로 작성한다. 각 문장에 시간대/대상 고객/상품·메뉴/운영 방식/날씨 대응/행사명 중 최소 2개 요소를 넣는다.
6. 세 액션은 역할이 겹치면 안 된다. 1개는 마케팅·노출, 1개는 상품·재고·프로모션, 1개는 운영·인력·동선 관점으로 작성한다.
7. "노출 강화", "피크 대응", "데이터 관찰"처럼 추상적인 표현만 단독으로 쓰지 않는다.
8. 실제 제공된 행사명·공연명·경기명·공휴일명·날씨 수치·지표 점수 중 가능한 근거를 액션에 반영한다.
9. market_weather, weather_icon, indicators 값은 변경하지 않는다.
10. 데이터가 부족하면 부족하다고 판단하되, 없는 사실을 만들지 않는다.
11. score_context.basis가 previous_evening_reference이면 모든 점수는 현재 심야 실측값이 아니라 직전 저녁 참고값이다. 현재 점수라고 표현하지 않는다.

[JSON 스키마]
{{
  "district": "{district}",
  "judgement_sentence": "판단 문장",
  "basis_sentence": "근거 문장",
  "recommended_actions": ["액션1", "액션2", "액션3"]
}}

[참고 데이터]
{json.dumps(llm_context, ensure_ascii=False, indent=2)}
""".strip()

    try:
        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content or ""
        raw_card = _extract_json(content)
        card = _merge_llm_with_rule_safety(raw_card, query, normalized_data, indicators)
        if return_generation_mode:
            return card, "hybrid_llm", None
        return card
    except Exception as exc:
        if allow_rule_fallback:
            card = build_market_feed_card(query, normalized_data, indicators)
            if return_generation_mode:
                return card, "rule_fallback", f"LLM 피드 생성 실패: {exc}"
            return card
        if isinstance(exc, LLMFeedError):
            raise
        raise LLMFeedError(f"LLM 피드 생성 실패: {exc}") from exc


# 이전 함수명과의 호환성 유지
def generate_llm_market_feed_card(
    query: dict[str, Any],
    normalized_data: dict[str, Any],
    indicators: IndicatorResult,
    model: str | None = None,
    api_key: str | None = None,
    item_limit: int = 5,
) -> dict[str, Any]:
    # Legacy arguments remain in the signature for call compatibility only.
    # Credentials and model selection intentionally come from the environment.
    return generate_hybrid_market_feed_card(
        query=query,
        normalized_data=normalized_data,
        indicators=indicators,
        item_limit=item_limit,
        allow_rule_fallback=True,
    )
