"""Production OA-21285 collector entry point.

Cron/worker에서 이 CLI를 주기적으로 호출한다. 매 실행은 대표 POI 한 건만
probe하고 PPLTN_TIME이 바뀐 경우에만 공식 83개 POI batch를 저장한다.
"""

from __future__ import annotations

import argparse
import json

from oa21285_config import OA21285_COLLECTION_INTERVAL_MINUTES
from oa21285_pilot import DEFAULT_PROBE_POI, run_pilot_once


def run_collection_once(**kwargs):
    """실패를 상태로 반환해 상권날씨 요청 경로와 collector 장애를 분리한다."""
    try:
        return run_pilot_once(**kwargs)
    except Exception as exc:  # collector process boundary
        return {"status": "failed", "api_call_count": 0, "error": str(exc), "batch_skipped": True}


def main() -> None:
    parser = argparse.ArgumentParser(description="OA-21285 probe-based production collector")
    parser.add_argument("--probe-poi", default=DEFAULT_PROBE_POI)
    parser.add_argument("--history-dir", default=None)
    parser.add_argument("--cache-dir", default=None)
    parser.add_argument("--full", action="store_true", help="수집된 자치구 상세 결과까지 출력")
    args = parser.parse_args()
    result = run_collection_once(probe_poi=args.probe_poi, history_dir=args.history_dir, cache_dir=args.cache_dir)
    result["configured_interval_minutes"] = OA21285_COLLECTION_INTERVAL_MINUTES
    if not args.full:
        result.pop("districts", None)
        result.pop("poi_coverage", None)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
