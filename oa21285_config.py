"""OA-21285 v1 aggregation, history, provider 설정."""

import os

# GIS 경계의 미세 sliver를 effective 후보에서 제외하기 위한 품질 threshold.
OA21285_MIN_INTERSECTION_RATIO = 0.01
OA21285_SINGLE_DISTRICT_RATIO = 0.99

# 배포 환경의 cron/worker가 이 간격으로 CLI를 호출한다. 애플리케이션 내부
# scheduler는 두지 않으며, 매 실행은 probe 한 번으로 batch 필요 여부를 판단한다.
OA21285_COLLECTION_INTERVAL_MINUTES = int(os.getenv("OA21285_COLLECTION_INTERVAL_MINUTES", "5"))
OA21285_SCHEDULER_ENABLED = False
OA21285_SCHEDULER_INTERVAL_MINUTES = OA21285_COLLECTION_INTERVAL_MINUTES

# Aggregation과 percentile adapter의 production 전환이 완료되었다.
OA21285_PRODUCTION_AGGREGATION_ENABLED = True
OA21285_FOOTFALL_ENABLED = True
OA21285_MIN_BAND_SNAPSHOTS = 4
# Backward-compatible name for callers; production quality threshold is 4.
OA21285_MIN_VALID_HISTORY_SNAPSHOTS = OA21285_MIN_BAND_SNAPSHOTS

OA21285_CATEGORY_WEIGHTS = {"발달상권": 1.0, "인구밀집지역": 1.0, "관광특구": 0.7}
OA21285_EXCLUDED_CATEGORIES = frozenset({"고궁·문화유산", "공원"})
OA21285_TIME_BANDS = {
    "심야": (0, 6), "아침": (6, 12), "점심": (12, 17),
    "오후": (17, 20), "저녁": (20, 24),
}
