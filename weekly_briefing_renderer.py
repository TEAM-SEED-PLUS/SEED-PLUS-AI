"""Renderer that describes computed weekly facts without inventing trends."""

import json
import os


def render_weekly_briefing(briefing: dict) -> str:
    average = briefing.get("weekly_average_score")
    period = briefing.get("period", {})
    completeness = briefing.get("completeness", "partial")
    sentences = [f"{period.get('start', '')}~{period.get('end', '')} 분석은 {completeness}이며, "
                 f"잠정 평균 기회 점수는 {average if average is not None else '자료 없음'}입니다."]
    vitality = briefing.get("weekly_vitality", {})
    if vitality.get("most_active_population_day"):
        sentences.append(f"확보한 관측에서 가장 활발한 날짜는 {vitality['most_active_population_day']}, "
                         f"가장 활발한 시간대는 {vitality['strongest_population_band']}입니다.")
    if vitality.get("population_change_pct") is not None:
        sentences.append(f"같은 요일·시간대 {vitality['matched_bands']}/{vitality['expected_bands']}개 구간의 "
                         f"생활인구 변화율 평균은 {vitality['population_change_pct']:+.2f}%입니다.")
    structural = briefing.get("structural_environment", {})
    if structural.get("estimated_sales") is not None:
        sentences.append(f"{structural['source_period']} {structural['administrative_dong']}의 "
                         f"분기 기준 추정매출은 {structural['estimated_sales']:,}원입니다.")
    nearby = briefing.get("nearby_competition", {})
    if nearby.get("total_store_count") is not None:
        sentences.append(f"{nearby['source_updated_at']} 상가 원본의 반경 점포 "
                         f"{nearby['total_store_count']:,}개는 구조적 경쟁 참고자료입니다.")
    return " ".join(sentences)


def generate_verified_narrative(briefing):
    """Let the model select the lead, while rendering every fact in Python."""
    fallback = {"generation_mode": "deterministic_fallback", "summary": render_weekly_briefing(briefing)}
    if not os.getenv("OPENAI_API_KEY") or not briefing["daily_scores"]:
        return {**fallback, "fallback_reason": "missing_api_key_or_observations"}
    facts = {"overview": fallback["summary"]}
    quality = briefing["data_quality"]
    if quality.get("strongest_population_band") and quality.get("population_spatial_grain") == "radius_grid_estimate":
        band = quality["strongest_population_band"]
        amount = quality["population_by_time_band"][band]
        facts["population"] = f"확보한 시간대 중 반경 추정 생활인구 평균이 가장 높은 시간대는 {band}({amount:,.1f}명)입니다."
    baseline = briefing["structural_baseline"]
    if baseline.get("estimated_sales") is not None:
        facts["quarter"] = f"{briefing['source_period']} 서교동 분기 추정매출은 {baseline['estimated_sales']:,}원입니다."
    try:
        from openai import OpenAI
        from llm_feed_writer import _extract_json

        client = OpenAI(api_key=os.environ["OPENAI_API_KEY"], timeout=15, max_retries=0)
        result = client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL") or "gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": '검증된 브리핑 문장을 읽기 좋은 순서로 배열하세요. 모든 ID를 정확히 한 번 포함하여 {"order": [...]} JSON만 반환하세요.'},
                      {"role": "user", "content": json.dumps(facts, ensure_ascii=False)}],
        )
        order = _extract_json(result.choices[0].message.content or "")["order"]
        if not isinstance(order, list) or len(order) != len(facts) or set(order) != facts.keys():
            return {**fallback, "fallback_reason": "invalid_fact_order"}
    except Exception as exc:
        return {**fallback, "fallback_reason": type(exc).__name__}
    return {"generation_mode": "llm_fact_ordering", "summary": " ".join(facts[key] for key in order)}
