"""Explicit weatherfeed-to-commercial-taxonomy mapping scaffold.

Codes remain empty until product/data review approves them.  Nothing in this
module participates in production scoring.
"""

from __future__ import annotations


WEATHERFEED_CATEGORY_MAPPING = {
    category: {
        "commercial_api_level": None,
        "commercial_api_codes": [],
        "status": "pending_review",
    }
    for category in (
        "음식점", "카페", "간편식", "디저트", "음료", "편의형 소매",
        "예약형 외식", "라이프스타일 소매", "배달·포장", "실내형 서비스",
        "야외형 F&B", "산책형 소비",
    )
}

