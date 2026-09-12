import json
import unittest

from feed_renderer import build_market_feed_card
from indicator_engine import IndicatorResult
from llm_feed_writer import build_llm_context
from normalized_city_data import _normalize_content_block


class NarrativeContentBriefTests(unittest.TestCase):
    def test_internal_provider_is_not_used_in_recommended_actions(self):
        festival = _normalize_content_block({"count": 1, "items": [{
            "title": "2026 국제선명상대회",
            "source": "TourAPI searchFestival2 openapi API endpoint",
            "place": "봉은사",
            "time_text": "18:00",
        }]}, "festival")
        data = {
            "query": {"district": "강남구", "time_band": "오후"},
            "weather": {}, "footfall": {"summary": {}},
            "special_day": {"count": 0, "items": []},
            "festival": festival,
            "event": {"count": 0, "items": [], "story_lines": []},
            "performance": {"count": 0, "items": [], "story_lines": []},
            "sports": {"count": 0, "items": [], "story_lines": []},
            "overview": {"content_tag_counts": {}, "llm_story_digest": festival["llm_briefs"]},
        }
        indicators = IndicatorResult(
            inflow_pressure=60, spending_intent=55, competition_pressure=30,
            operational_risk=20, opportunity_score=62, market_weather="흐림",
            market_weather_emoji="☁️", decision_tags=["관망 권장"], evidence={},
            signal_flags=[], recommended_categories=[], avoid_reasons=[], contribution_map={},
        )
        card = build_market_feed_card(data["query"], data, indicators)
        actions = " ".join(card["recommended_actions"])
        self.assertIn("2026 국제선명상대회(봉은사)", actions)
        for internal in ("TourAPI", "searchFestival2", "KOPIS", "OA-", "openapi", "API endpoint"):
            self.assertNotIn(internal, actions)

        # The same sanitized brief is supplied to the optional LLM path.
        context = json.dumps(build_llm_context(data["query"], data, indicators), ensure_ascii=False)
        self.assertIn("2026 국제선명상대회", context)
        for internal in ("TourAPI", "searchFestival2", "KOPIS", "OA-", "openapi", "API endpoint"):
            self.assertNotIn(internal, context)


if __name__ == "__main__":
    unittest.main()
