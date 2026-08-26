# 상권날씨 v1 최종 통합 QA (2026-08-23)

**Production readiness: `ready`**

## 1. Production config

```json
{
  "opportunity_weights": {
    "inflow": 0.35,
    "spending": 0.3,
    "inverse_competition": 0.15,
    "inverse_risk": 0.2
  },
  "weather_thresholds": {
    "clear": 80,
    "cloud": 65,
    "overcast": 50,
    "rain": 35
  },
  "time_bands": {
    "심야": [
      0,
      6
    ],
    "아침": [
      6,
      12
    ],
    "점심": [
      12,
      17
    ],
    "오후": [
      17,
      20
    ],
    "저녁": [
      20,
      24
    ]
  },
  "deep_night": {
    "range": "00:00-06:00",
    "reference": "previous_calendar_day 20:00",
    "badge": "심야 시간대는 데이터가 제한적입니다 (06시부터 갱신)"
  },
  "missing_data": "base retained; unavailable source bonus skipped; explicit legacy fallback only",
  "feature_flags": {
    "tourism": true,
    "oa21285_footfall": true,
    "commercial_store_competition": true,
    "consumption_hybrid": true,
    "structural_closure_risk": false
  },
  "caps": {
    "inflow": {
      "content": 26,
      "oa_level": 22.0,
      "oa_peak": 8.0,
      "tourapi": 10.0
    },
    "spending": {
      "content": 24,
      "weather_positive": 10,
      "special_day": 10,
      "sales_baseline": 7.0,
      "realtime_commerce": 3.0,
      "tourapi": 10.0
    },
    "competition": {
      "content": 25,
      "commercial_store": 12.0,
      "sdot_realtime": 4.0
    },
    "risk": {
      "base": 20,
      "content": 12,
      "weather": 54,
      "special_day": 8
    }
  }
}
```

## 2. Source freshness

```json
{
  "oa21285": {
    "latest_population_time": "2026-08-22 21:25",
    "band_snapshot_count": 5,
    "age_minutes_at_qa": 1170.54,
    "available_district_count": 24,
    "unavailable_districts": [
      "중랑구"
    ]
  },
  "commercial_store": {
    "source_quarter": "2026Q3",
    "snapshot_id": "commercial-stores-2026Q3",
    "snapshot_quarter": "2026Q3",
    "started_at": "2026-08-23T14:53:03+09:00",
    "completed_at": "2026-08-23T14:58:35+09:00",
    "district_count": 25,
    "completed_district_count": 25,
    "partial_district_count": 0,
    "failed_district_count": 0,
    "api_call_count": 568,
    "raw_record_count": 554092,
    "deduplicated_record_count": 554092,
    "duplicate_count": 0,
    "null_coordinate_count": 0,
    "null_industry_count": 0,
    "unknown_district_count": 0,
    "large_count": 10,
    "middle_count": 75,
    "small_count": 247,
    "source_status": "complete"
  },
  "consumption_baseline": {
    "source": "OA-22176+OA-22173",
    "denominator_source": "seoul_commercial_analysis_OA22173",
    "requested_quarter": "2026Q3",
    "source_quarter": "2026Q1",
    "age_quarters": 2,
    "source_status": "fallback",
    "sales_per_matched_store": 58563166.544240184,
    "percentile": 83.33,
    "bonus": 5.8331,
    "semantics": "서울시 추정매출과 동일 업종 모집단 점포수를 이용한 자치구 상대 소비 수준"
  },
  "oa22385": {
    "latest_commerce_time": "20260823 1600",
    "received_at": "2026-08-23T16:14:32+09:00",
    "place_count": 82,
    "valid_place_count": 82,
    "failed_place_count": 0,
    "source_status": "ok",
    "age_minutes_at_qa": 55.54,
    "district_coverage": 24,
    "no_data_districts": [
      "중랑구"
    ]
  },
  "tourapi": {
    "requested_month": "202608",
    "source_month": "202509",
    "fallback_month_distance": 11,
    "district_coverage": 25,
    "snapshot_status": "complete"
  },
  "structural_risk": {
    "enabled": false,
    "status": "deferred",
    "production_applied": false,
    "recommended_metric": "recent_4_complete_quarters_pooled_closure_rate_percentile",
    "recommended_base_structure": "fixed10_plus_structural_max10",
    "reason_codes": [
      "no_current_official_district_refresh_source",
      "kosis_no_seoul_district_grain",
      "legacy_seoul_dataset_discontinued"
    ]
  }
}
```

QA smoke 기준은 2026-08-22 21:25 저장 snapshot replay다. 네트워크 결측값은 생성하지 않았다. OA-22385 최신 상태는 별도 freshness에 표시되지만 날짜가 다른 smoke 점수에는 가산하지 않았다.

## 3. 서울 25개구 final score

| 자치구 | 유입 | 소비 | 경쟁 | 리스크 | 날씨 | 태그 | 유입 provider | 소비 mode | 경쟁 mode | fallback |
|---|---:|---:|---:|---:|---|---|---|---|---|---|
| 강남구 | 48 | 30 | 29 | 20 | 흐림 | 관망 권장 | oa21285 | sales_baseline_only | commercial_only | True |
| 강동구 | 28 | 28 | 22 | 20 | 비 | 관망 권장 | oa21285 | sales_baseline_only | commercial_only | True |
| 강북구 | 36 | 28 | 19 | 20 | 비 | 관망 권장 | oa21285 | sales_baseline_only | commercial_only | True |
| 강서구 | 25 | 25 | 20 | 20 | 비 | 관망 권장 | oa21285 | sales_baseline_only | commercial_only | True |
| 관악구 | 38 | 25 | 21 | 20 | 비 | 관망 권장 | oa21285 | sales_baseline_only | commercial_only | True |
| 광진구 | 46 | 28 | 27 | 20 | 흐림 | 관망 권장 | oa21285 | sales_baseline_only | commercial_only | True |
| 구로구 | 30 | 26 | 26 | 20 | 비 | 관망 권장 | oa21285 | sales_baseline_only | commercial_only | True |
| 금천구 | 34 | 30 | 29 | 20 | 비 | 관망 권장 | oa21285 | sales_baseline_only | commercial_only | True |
| 노원구 | 48 | 27 | 18 | 20 | 흐림 | 관망 권장 | oa21285 | sales_baseline_only | commercial_only | True |
| 도봉구 | 25 | 27 | 19 | 20 | 비 | 관망 권장 | oa21285 | sales_baseline_only | commercial_only | True |
| 동대문구 | 32 | 31 | 28 | 20 | 비 | 관망 권장 | oa21285 | sales_baseline_only | commercial_only | True |
| 동작구 | 43 | 31 | 24 | 20 | 흐림 | 관망 권장 | oa21285 | sales_baseline_only | commercial_only | True |
| 마포구 | 52 | 25 | 28 | 20 | 흐림 | 관망 권장 | oa21285 | sales_baseline_only | commercial_only | True |
| 서대문구 | 45 | 26 | 22 | 20 | 흐림 | 관망 권장 | oa21285 | sales_baseline_only | commercial_only | True |
| 서초구 | 36 | 29 | 23 | 20 | 비 | 관망 권장 | oa21285 | sales_baseline_only | commercial_only | True |
| 성동구 | 42 | 25 | 26 | 20 | 비 | 관망 권장 | oa21285 | sales_baseline_only | commercial_only | True |
| 성북구 | 28 | 24 | 20 | 20 | 비 | 관망 권장 | oa21285 | sales_baseline_only | commercial_only | True |
| 송파구 | 53 | 30 | 26 | 20 | 흐림 | 관망 권장 | oa21285 | sales_baseline_only | commercial_only | True |
| 양천구 | 54 | 26 | 25 | 20 | 흐림 | 관망 권장 | oa21285 | sales_baseline_only | commercial_only | True |
| 영등포구 | 51 | 29 | 28 | 20 | 흐림 | 관망 권장 | oa21285 | sales_baseline_only | commercial_only | True |
| 용산구 | 39 | 30 | 22 | 20 | 흐림 | 관망 권장 | oa21285 | sales_baseline_only | commercial_only | True |
| 은평구 | 30 | 24 | 20 | 20 | 비 | 관망 권장 | oa21285 | sales_baseline_only | commercial_only | True |
| 종로구 | 37 | 29 | 25 | 20 | 비 | 관망 권장 | oa21285 | sales_baseline_only | commercial_only | True |
| 중구 | 42 | 28 | 30 | 20 | 흐림 | 관망 권장 | oa21285 | sales_baseline_only | commercial_only | True |
| 중랑구 | 24 | 27 | 23 | 20 | 비 | 관망 권장 | sdot | sales_baseline_only | commercial_only | True |

## 4. Contribution decomposition

아래 JSON은 각 구별 `sum → production clamp → final` 재구성에 사용한 실제 항목이다.

```json
{
  "강남구": {
    "inflow": {
      "base": 24,
      "content": 0.0,
      "oa_level": 17.7914,
      "oa_peak": 6.6088,
      "sdot_fallback": 0,
      "weather": 0.0,
      "holiday": 0.0,
      "tourapi": 0.0
    },
    "spending": {
      "base": 24,
      "content": 0.0,
      "consumption_baseline": 5.8331,
      "realtime_commerce": 0,
      "oa_legacy_fallback": 0.0,
      "weather": 0.0,
      "holiday": 0.0,
      "tourapi": 0.0
    },
    "competition": {
      "base": 18,
      "content": 0.0,
      "commercial_store": 11.4996,
      "sdot_realtime": 0.0
    },
    "risk": {
      "base": 20,
      "content": 0.0,
      "weather": 0.0,
      "special_day": 0.0,
      "structural": 0.0
    }
  },
  "강동구": {
    "inflow": {
      "base": 24,
      "content": 0.0,
      "oa_level": 2.6774,
      "oa_peak": 1.0432,
      "sdot_fallback": 0,
      "weather": 0.0,
      "holiday": 0.0,
      "tourapi": 0.0
    },
    "spending": {
      "base": 24,
      "content": 0.0,
      "consumption_baseline": 3.5,
      "realtime_commerce": 0,
      "oa_legacy_fallback": 0.0,
      "weather": 0.0,
      "holiday": 0.0,
      "tourapi": 0.0
    },
    "competition": {
      "base": 18,
      "content": 0.0,
      "commercial_store": 4.5,
      "sdot_realtime": 0.0
    },
    "risk": {
      "base": 20,
      "content": 0.0,
      "weather": 0.0,
      "special_day": 0.0,
      "structural": 0.0
    }
  },
  "강북구": {
    "inflow": {
      "base": 24,
      "content": 0.0,
      "oa_level": 8.4172,
      "oa_peak": 3.4784,
      "sdot_fallback": 0,
      "weather": 0.0,
      "holiday": 0.0,
      "tourapi": 0.0
    },
    "spending": {
      "base": 24,
      "content": 0.0,
      "consumption_baseline": 4.375,
      "realtime_commerce": 0,
      "oa_legacy_fallback": 0.0,
      "weather": 0.0,
      "holiday": 0.0,
      "tourapi": 0.0
    },
    "competition": {
      "base": 18,
      "content": 0.0,
      "commercial_store": 0.9996,
      "sdot_realtime": 0.0
    },
    "risk": {
      "base": 20,
      "content": 0.0,
      "weather": 0.0,
      "special_day": 0.0,
      "structural": 0.0
    }
  },
  "강서구": {
    "inflow": {
      "base": 24,
      "content": 0.0,
      "oa_level": 0.5742,
      "oa_peak": 0.348,
      "sdot_fallback": 0,
      "weather": 0.0,
      "holiday": 0.0,
      "tourapi": 0.0
    },
    "spending": {
      "base": 24,
      "content": 0.0,
      "consumption_baseline": 1.1669,
      "realtime_commerce": 0,
      "oa_legacy_fallback": 0.0,
      "weather": 0.0,
      "holiday": 0.0,
      "tourapi": 0.0
    },
    "competition": {
      "base": 18,
      "content": 0.0,
      "commercial_store": 2.4996,
      "sdot_realtime": 0.0
    },
    "risk": {
      "base": 20,
      "content": 0.0,
      "weather": 0.0,
      "special_day": 0.0,
      "structural": 0.0
    }
  },
  "관악구": {
    "inflow": {
      "base": 24,
      "content": 0.0,
      "oa_level": 10.1398,
      "oa_peak": 4.1736,
      "sdot_fallback": 0,
      "weather": 0.0,
      "holiday": 0.0,
      "tourapi": 0.0
    },
    "spending": {
      "base": 24,
      "content": 0.0,
      "consumption_baseline": 0.875,
      "realtime_commerce": 0,
      "oa_legacy_fallback": 0.0,
      "weather": 0.0,
      "holiday": 0.0,
      "tourapi": 0.0
    },
    "competition": {
      "base": 18,
      "content": 0.0,
      "commercial_store": 3.0,
      "sdot_realtime": 0.0
    },
    "risk": {
      "base": 20,
      "content": 0.0,
      "weather": 0.0,
      "special_day": 0.0,
      "structural": 0.0
    }
  },
  "광진구": {
    "inflow": {
      "base": 24,
      "content": 0.0,
      "oa_level": 16.2602,
      "oa_peak": 5.9128,
      "sdot_fallback": 0,
      "weather": 0.0,
      "holiday": 0.0,
      "tourapi": 0.0
    },
    "spending": {
      "base": 24,
      "content": 0.0,
      "consumption_baseline": 3.7919,
      "realtime_commerce": 0,
      "oa_legacy_fallback": 0.0,
      "weather": 0.0,
      "holiday": 0.0,
      "tourapi": 0.0
    },
    "competition": {
      "base": 18,
      "content": 0.0,
      "commercial_store": 9.0,
      "sdot_realtime": 0.0
    },
    "risk": {
      "base": 20,
      "content": 0.0,
      "weather": 0.0,
      "special_day": 0.0,
      "structural": 0.0
    }
  },
  "구로구": {
    "inflow": {
      "base": 24,
      "content": 0.0,
      "oa_level": 3.8258,
      "oa_peak": 1.7392,
      "sdot_fallback": 0,
      "weather": 0.0,
      "holiday": 0.0,
      "tourapi": 0.0
    },
    "spending": {
      "base": 24,
      "content": 0.0,
      "consumption_baseline": 2.0419,
      "realtime_commerce": 0,
      "oa_legacy_fallback": 0.0,
      "weather": 0.0,
      "holiday": 0.0,
      "tourapi": 0.0
    },
    "competition": {
      "base": 18,
      "content": 0.0,
      "commercial_store": 7.5,
      "sdot_realtime": 0.0
    },
    "risk": {
      "base": 20,
      "content": 0.0,
      "weather": 0.0,
      "special_day": 0.0,
      "structural": 0.0
    }
  },
  "금천구": {
    "inflow": {
      "base": 24,
      "content": 0.0,
      "oa_level": 6.886,
      "oa_peak": 2.7824,
      "sdot_fallback": 0,
      "weather": 0.0,
      "holiday": 0.0,
      "tourapi": 0.0
    },
    "spending": {
      "base": 24,
      "content": 0.0,
      "consumption_baseline": 6.4169,
      "realtime_commerce": 0,
      "oa_legacy_fallback": 0.0,
      "weather": 0.0,
      "holiday": 0.0,
      "tourapi": 0.0
    },
    "competition": {
      "base": 18,
      "content": 0.0,
      "commercial_store": 11.0004,
      "sdot_realtime": 0.0
    },
    "risk": {
      "base": 20,
      "content": 0.0,
      "weather": 0.0,
      "special_day": 0.0,
      "structural": 0.0
    }
  },
  "노원구": {
    "inflow": {
      "base": 24,
      "content": 0.0,
      "oa_level": 17.6,
      "oa_peak": 6.6088,
      "sdot_fallback": 0,
      "weather": 0.0,
      "holiday": 0.0,
      "tourapi": 0.0
    },
    "spending": {
      "base": 24,
      "content": 0.0,
      "consumption_baseline": 3.2081,
      "realtime_commerce": 0,
      "oa_legacy_fallback": 0.0,
      "weather": 0.0,
      "holiday": 0.0,
      "tourapi": 0.0
    },
    "competition": {
      "base": 18,
      "content": 0.0,
      "commercial_store": 0.0,
      "sdot_realtime": 0.0
    },
    "risk": {
      "base": 20,
      "content": 0.0,
      "weather": 0.0,
      "special_day": 0.0,
      "structural": 0.0
    }
  },
  "도봉구": {
    "inflow": {
      "base": 24,
      "content": 0.0,
      "oa_level": 0.3828,
      "oa_peak": 0.348,
      "sdot_fallback": 0,
      "weather": 0.0,
      "holiday": 0.0,
      "tourapi": 0.0
    },
    "spending": {
      "base": 24,
      "content": 0.0,
      "consumption_baseline": 2.9169,
      "realtime_commerce": 0,
      "oa_legacy_fallback": 0.0,
      "weather": 0.0,
      "holiday": 0.0,
      "tourapi": 0.0
    },
    "competition": {
      "base": 18,
      "content": 0.0,
      "commercial_store": 0.5004,
      "sdot_realtime": 0.0
    },
    "risk": {
      "base": 20,
      "content": 0.0,
      "weather": 0.0,
      "special_day": 0.0,
      "structural": 0.0
    }
  },
  "동대문구": {
    "inflow": {
      "base": 24,
      "content": 0.0,
      "oa_level": 5.7398,
      "oa_peak": 2.0872,
      "sdot_fallback": 0,
      "weather": 0.0,
      "holiday": 0.0,
      "tourapi": 0.0
    },
    "spending": {
      "base": 24,
      "content": 0.0,
      "consumption_baseline": 6.7081,
      "realtime_commerce": 0,
      "oa_legacy_fallback": 0.0,
      "weather": 0.0,
      "holiday": 0.0,
      "tourapi": 0.0
    },
    "competition": {
      "base": 18,
      "content": 0.0,
      "commercial_store": 9.9996,
      "sdot_realtime": 0.0
    },
    "risk": {
      "base": 20,
      "content": 0.0,
      "weather": 0.0,
      "special_day": 0.0,
      "structural": 0.0
    }
  },
  "동작구": {
    "inflow": {
      "base": 24,
      "content": 0.0,
      "oa_level": 14.157,
      "oa_peak": 5.2176,
      "sdot_fallback": 0,
      "weather": 0.0,
      "holiday": 0.0,
      "tourapi": 0.0
    },
    "spending": {
      "base": 24,
      "content": 0.0,
      "consumption_baseline": 7.0,
      "realtime_commerce": 0,
      "oa_legacy_fallback": 0.0,
      "weather": 0.0,
      "holiday": 0.0,
      "tourapi": 0.0
    },
    "competition": {
      "base": 18,
      "content": 0.0,
      "commercial_store": 6.0,
      "sdot_realtime": 0.0
    },
    "risk": {
      "base": 20,
      "content": 0.0,
      "weather": 0.0,
      "special_day": 0.0,
      "structural": 0.0
    }
  },
  "마포구": {
    "inflow": {
      "base": 24,
      "content": 0.0,
      "oa_level": 20.086,
      "oa_peak": 7.652,
      "sdot_fallback": 0,
      "weather": 0.0,
      "holiday": 0.0,
      "tourapi": 0.0
    },
    "spending": {
      "base": 24,
      "content": 0.0,
      "consumption_baseline": 1.4581,
      "realtime_commerce": 0,
      "oa_legacy_fallback": 0.0,
      "weather": 0.0,
      "holiday": 0.0,
      "tourapi": 0.0
    },
    "competition": {
      "base": 18,
      "content": 0.0,
      "commercial_store": 10.5,
      "sdot_realtime": 0.0
    },
    "risk": {
      "base": 20,
      "content": 0.0,
      "weather": 0.0,
      "special_day": 0.0,
      "structural": 0.0
    }
  },
  "서대문구": {
    "inflow": {
      "base": 24,
      "content": 0.0,
      "oa_level": 15.3054,
      "oa_peak": 5.5656,
      "sdot_fallback": 0,
      "weather": 0.0,
      "holiday": 0.0,
      "tourapi": 0.0
    },
    "spending": {
      "base": 24,
      "content": 0.0,
      "consumption_baseline": 2.3331,
      "realtime_commerce": 0,
      "oa_legacy_fallback": 0.0,
      "weather": 0.0,
      "holiday": 0.0,
      "tourapi": 0.0
    },
    "competition": {
      "base": 18,
      "content": 0.0,
      "commercial_store": 3.9996,
      "sdot_realtime": 0.0
    },
    "risk": {
      "base": 20,
      "content": 0.0,
      "weather": 0.0,
      "special_day": 0.0,
      "structural": 0.0
    }
  },
  "서초구": {
    "inflow": {
      "base": 24,
      "content": 0.0,
      "oa_level": 8.2258,
      "oa_peak": 3.8264,
      "sdot_fallback": 0,
      "weather": 0.0,
      "holiday": 0.0,
      "tourapi": 0.0
    },
    "spending": {
      "base": 24,
      "content": 0.0,
      "consumption_baseline": 4.6669,
      "realtime_commerce": 0,
      "oa_legacy_fallback": 0.0,
      "weather": 0.0,
      "holiday": 0.0,
      "tourapi": 0.0
    },
    "competition": {
      "base": 18,
      "content": 0.0,
      "commercial_store": 5.4996,
      "sdot_realtime": 0.0
    },
    "risk": {
      "base": 20,
      "content": 0.0,
      "weather": 0.0,
      "special_day": 0.0,
      "structural": 0.0
    }
  },
  "성동구": {
    "inflow": {
      "base": 24,
      "content": 0.0,
      "oa_level": 13.0086,
      "oa_peak": 4.8696,
      "sdot_fallback": 0,
      "weather": 0.0,
      "holiday": 0.0,
      "tourapi": 0.0
    },
    "spending": {
      "base": 24,
      "content": 0.0,
      "consumption_baseline": 0.5831,
      "realtime_commerce": 0,
      "oa_legacy_fallback": 0.0,
      "weather": 0.0,
      "holiday": 0.0,
      "tourapi": 0.0
    },
    "competition": {
      "base": 18,
      "content": 0.0,
      "commercial_store": 8.4996,
      "sdot_realtime": 0.0
    },
    "risk": {
      "base": 20,
      "content": 0.0,
      "weather": 0.0,
      "special_day": 0.0,
      "structural": 0.0
    }
  },
  "성북구": {
    "inflow": {
      "base": 24,
      "content": 0.0,
      "oa_level": 2.2968,
      "oa_peak": 1.3912,
      "sdot_fallback": 0,
      "weather": 0.0,
      "holiday": 0.0,
      "tourapi": 0.0
    },
    "spending": {
      "base": 24,
      "content": 0.0,
      "consumption_baseline": 0.0,
      "realtime_commerce": 0,
      "oa_legacy_fallback": 0.0,
      "weather": 0.0,
      "holiday": 0.0,
      "tourapi": 0.0
    },
    "competition": {
      "base": 18,
      "content": 0.0,
      "commercial_store": 2.0004,
      "sdot_realtime": 0.0
    },
    "risk": {
      "base": 20,
      "content": 0.0,
      "weather": 0.0,
      "special_day": 0.0,
      "structural": 0.0
    }
  },
  "송파구": {
    "inflow": {
      "base": 24,
      "content": 0.0,
      "oa_level": 20.8516,
      "oa_peak": 7.652,
      "sdot_fallback": 0,
      "weather": 0.0,
      "holiday": 0.0,
      "tourapi": 0.0
    },
    "spending": {
      "base": 24,
      "content": 0.0,
      "consumption_baseline": 5.5419,
      "realtime_commerce": 0,
      "oa_legacy_fallback": 0.0,
      "weather": 0.0,
      "holiday": 0.0,
      "tourapi": 0.0
    },
    "competition": {
      "base": 18,
      "content": 0.0,
      "commercial_store": 8.0004,
      "sdot_realtime": 0.0
    },
    "risk": {
      "base": 20,
      "content": 0.0,
      "weather": 0.0,
      "special_day": 0.0,
      "structural": 0.0
    }
  },
  "양천구": {
    "inflow": {
      "base": 24,
      "content": 0.0,
      "oa_level": 22.0,
      "oa_peak": 8.0,
      "sdot_fallback": 0,
      "weather": 0.0,
      "holiday": 0.0,
      "tourapi": 0.0
    },
    "spending": {
      "base": 24,
      "content": 0.0,
      "consumption_baseline": 1.75,
      "realtime_commerce": 0,
      "oa_legacy_fallback": 0.0,
      "weather": 0.0,
      "holiday": 0.0,
      "tourapi": 0.0
    },
    "competition": {
      "base": 18,
      "content": 0.0,
      "commercial_store": 6.9996,
      "sdot_realtime": 0.0
    },
    "risk": {
      "base": 20,
      "content": 0.0,
      "weather": 0.0,
      "special_day": 0.0,
      "structural": 0.0
    }
  },
  "영등포구": {
    "inflow": {
      "base": 24,
      "content": 0.0,
      "oa_level": 19.3226,
      "oa_peak": 7.304,
      "sdot_fallback": 0,
      "weather": 0.0,
      "holiday": 0.0,
      "tourapi": 0.0
    },
    "spending": {
      "base": 24,
      "content": 0.0,
      "consumption_baseline": 5.25,
      "realtime_commerce": 0,
      "oa_legacy_fallback": 0.0,
      "weather": 0.0,
      "holiday": 0.0,
      "tourapi": 0.0
    },
    "competition": {
      "base": 18,
      "content": 0.0,
      "commercial_store": 9.5004,
      "sdot_realtime": 0.0
    },
    "risk": {
      "base": 20,
      "content": 0.0,
      "weather": 0.0,
      "special_day": 0.0,
      "structural": 0.0
    }
  },
  "용산구": {
    "inflow": {
      "base": 24,
      "content": 0.0,
      "oa_level": 11.286,
      "oa_peak": 4.1736,
      "sdot_fallback": 0,
      "weather": 0.0,
      "holiday": 0.0,
      "tourapi": 0.0
    },
    "spending": {
      "base": 24,
      "content": 0.0,
      "consumption_baseline": 6.125,
      "realtime_commerce": 0,
      "oa_legacy_fallback": 0.0,
      "weather": 0.0,
      "holiday": 0.0,
      "tourapi": 0.0
    },
    "competition": {
      "base": 18,
      "content": 0.0,
      "commercial_store": 3.5004,
      "sdot_realtime": 0.0
    },
    "risk": {
      "base": 20,
      "content": 0.0,
      "weather": 0.0,
      "special_day": 0.0,
      "structural": 0.0
    }
  },
  "은평구": {
    "inflow": {
      "base": 24,
      "content": 0.0,
      "oa_level": 4.5914,
      "oa_peak": 1.7392,
      "sdot_fallback": 0,
      "weather": 0.0,
      "holiday": 0.0,
      "tourapi": 0.0
    },
    "spending": {
      "base": 24,
      "content": 0.0,
      "consumption_baseline": 0.2919,
      "realtime_commerce": 0,
      "oa_legacy_fallback": 0.0,
      "weather": 0.0,
      "holiday": 0.0,
      "tourapi": 0.0
    },
    "competition": {
      "base": 18,
      "content": 0.0,
      "commercial_store": 1.5,
      "sdot_realtime": 0.0
    },
    "risk": {
      "base": 20,
      "content": 0.0,
      "weather": 0.0,
      "special_day": 0.0,
      "structural": 0.0
    }
  },
  "종로구": {
    "inflow": {
      "base": 24,
      "content": 0.0,
      "oa_level": 9.5656,
      "oa_peak": 3.8264,
      "sdot_fallback": 0,
      "weather": 0.0,
      "holiday": 0.0,
      "tourapi": 0.0
    },
    "spending": {
      "base": 24,
      "content": 0.0,
      "consumption_baseline": 4.9581,
      "realtime_commerce": 0,
      "oa_legacy_fallback": 0.0,
      "weather": 0.0,
      "holiday": 0.0,
      "tourapi": 0.0
    },
    "competition": {
      "base": 18,
      "content": 0.0,
      "commercial_store": 6.5004,
      "sdot_realtime": 0.0
    },
    "risk": {
      "base": 20,
      "content": 0.0,
      "weather": 0.0,
      "special_day": 0.0,
      "structural": 0.0
    }
  },
  "중구": {
    "inflow": {
      "base": 24,
      "content": 0.0,
      "oa_level": 13.0086,
      "oa_peak": 5.2176,
      "sdot_fallback": 0,
      "weather": 0.0,
      "holiday": 0.0,
      "tourapi": 0.0
    },
    "spending": {
      "base": 24,
      "content": 0.0,
      "consumption_baseline": 4.0831,
      "realtime_commerce": 0,
      "oa_legacy_fallback": 0.0,
      "weather": 0.0,
      "holiday": 0.0,
      "tourapi": 0.0
    },
    "competition": {
      "base": 18,
      "content": 0.0,
      "commercial_store": 12.0,
      "sdot_realtime": 0.0
    },
    "risk": {
      "base": 20,
      "content": 0.0,
      "weather": 0.0,
      "special_day": 0.0,
      "structural": 0.0
    }
  },
  "중랑구": {
    "inflow": {
      "base": 24,
      "content": 0.0,
      "oa_level": 0,
      "oa_peak": 0,
      "sdot_fallback": 0.0,
      "weather": 0.0,
      "holiday": 0.0,
      "tourapi": 0.0
    },
    "spending": {
      "base": 24,
      "content": 0.0,
      "consumption_baseline": 2.625,
      "realtime_commerce": 0,
      "oa_legacy_fallback": 0.0,
      "weather": 0.0,
      "holiday": 0.0,
      "tourapi": 0.0
    },
    "competition": {
      "base": 18,
      "content": 0.0,
      "commercial_store": 5.0004,
      "sdot_realtime": 0.0
    },
    "risk": {
      "base": 20,
      "content": 0.0,
      "weather": 0.0,
      "special_day": 0.0,
      "structural": 0.0
    }
  }
}
```

## 5. Score distribution

```json
{
  "inflow_pressure": {
    "mean": 38.64,
    "median": 38,
    "std": 9.23,
    "min": 24,
    "max": 54,
    "p25": 30,
    "p75": 46,
    "near_zero_count": 0,
    "near_100_count": 0,
    "largest_tie_count": 2,
    "unique_count": 19,
    "near_zero_variance": false,
    "serious_saturation": false
  },
  "spending_intent": {
    "mean": 27.52,
    "median": 28,
    "std": 2.138,
    "min": 24,
    "max": 31,
    "p25": 26,
    "p75": 29,
    "near_zero_count": 0,
    "near_100_count": 0,
    "largest_tie_count": 4,
    "unique_count": 8,
    "near_zero_variance": false,
    "serious_saturation": false
  },
  "competition_pressure": {
    "mean": 24,
    "median": 24,
    "std": 3.544,
    "min": 18,
    "max": 30,
    "p25": 21,
    "p75": 27,
    "near_zero_count": 0,
    "near_100_count": 0,
    "largest_tie_count": 3,
    "unique_count": 13,
    "near_zero_variance": false,
    "serious_saturation": false
  },
  "operational_risk": {
    "mean": 20,
    "median": 20,
    "std": 0.0,
    "min": 20,
    "max": 20,
    "p25": 20,
    "p75": 20,
    "near_zero_count": 0,
    "near_100_count": 0,
    "largest_tie_count": 25,
    "unique_count": 1,
    "near_zero_variance": true,
    "serious_saturation": false
  }
}
```

## 6. Top / bottom

순위 사유는 machine artifact의 동일 행 contribution으로만 해석한다.

```json
{
  "inflow_pressure": {
    "top": [
      {
        "district": "양천구",
        "score": 54
      },
      {
        "district": "송파구",
        "score": 53
      },
      {
        "district": "마포구",
        "score": 52
      },
      {
        "district": "영등포구",
        "score": 51
      },
      {
        "district": "강남구",
        "score": 48
      }
    ],
    "bottom": [
      {
        "district": "중랑구",
        "score": 24
      },
      {
        "district": "강서구",
        "score": 25
      },
      {
        "district": "도봉구",
        "score": 25
      },
      {
        "district": "강동구",
        "score": 28
      },
      {
        "district": "성북구",
        "score": 28
      }
    ]
  },
  "spending_intent": {
    "top": [
      {
        "district": "동대문구",
        "score": 31
      },
      {
        "district": "동작구",
        "score": 31
      },
      {
        "district": "강남구",
        "score": 30
      },
      {
        "district": "금천구",
        "score": 30
      },
      {
        "district": "송파구",
        "score": 30
      }
    ],
    "bottom": [
      {
        "district": "성북구",
        "score": 24
      },
      {
        "district": "은평구",
        "score": 24
      },
      {
        "district": "강서구",
        "score": 25
      },
      {
        "district": "관악구",
        "score": 25
      },
      {
        "district": "마포구",
        "score": 25
      }
    ]
  },
  "competition_pressure": {
    "top": [
      {
        "district": "중구",
        "score": 30
      },
      {
        "district": "강남구",
        "score": 29
      },
      {
        "district": "금천구",
        "score": 29
      },
      {
        "district": "동대문구",
        "score": 28
      },
      {
        "district": "마포구",
        "score": 28
      }
    ],
    "bottom": [
      {
        "district": "노원구",
        "score": 18
      },
      {
        "district": "강북구",
        "score": 19
      },
      {
        "district": "도봉구",
        "score": 19
      },
      {
        "district": "강서구",
        "score": 20
      },
      {
        "district": "성북구",
        "score": 20
      }
    ]
  },
  "operational_risk": {
    "top": [
      {
        "district": "강남구",
        "score": 20
      },
      {
        "district": "강동구",
        "score": 20
      },
      {
        "district": "강북구",
        "score": 20
      },
      {
        "district": "강서구",
        "score": 20
      },
      {
        "district": "관악구",
        "score": 20
      }
    ],
    "bottom": [
      {
        "district": "강남구",
        "score": 20
      },
      {
        "district": "강동구",
        "score": 20
      },
      {
        "district": "강북구",
        "score": 20
      },
      {
        "district": "강서구",
        "score": 20
      },
      {
        "district": "관악구",
        "score": 20
      }
    ]
  }
}
```

## 7. Cross-indicator correlation

```json
{
  "inflow_pressure_vs_spending_intent": {
    "pearson": 0.2082,
    "spearman": 0.2152
  },
  "inflow_pressure_vs_competition_pressure": {
    "pearson": 0.4378,
    "spearman": 0.4186
  },
  "spending_intent_vs_competition_pressure": {
    "pearson": 0.4277,
    "spearman": 0.434
  },
  "spending_intent_vs_operational_risk": {
    "pearson": null,
    "spearman": null
  },
  "competition_pressure_vs_operational_risk": {
    "pearson": null,
    "spearman": null
  }
}
```

유입-소비는 복제품 수준이 아니고 경쟁도 유입 복제가 아니다. 리스크는 이번 replay에서 모두 base20이라 상관계수가 정의되지 않는다.

## 8. Provider / fallback 및 double counting

OA-21285는 유입에 사용되고 정상 consumption hybrid에서는 소비에 중복 가산되지 않았다. baseline 실패 때만 legacy OA 소비 fallback이다. TourAPI 결측, realtime 결측, commercial realtime 결측 시 남은 component cap을 재분배하지 않는다. 명시적 legacy fallback만 예외다.

### Fallback matrix

```json
{
  "A_oa_failure_to_sdot": {
    "scores": {
      "inflow": 52,
      "spending": 34,
      "competition": 32,
      "risk": 20
    },
    "footfall_provider": "sdot",
    "spending_mode": "legacy_oa_spending_fallback",
    "competition_mode": "commercial_plus_realtime",
    "tourism_status": "no_data",
    "data_insufficient": true
  },
  "B_oa_no_data_district_to_sdot": {
    "scores": {
      "inflow": 52,
      "spending": 34,
      "competition": 25,
      "risk": 20
    },
    "footfall_provider": "sdot",
    "spending_mode": "legacy_oa_spending_fallback",
    "competition_mode": "commercial_plus_realtime",
    "tourism_status": "no_data",
    "data_insufficient": true
  },
  "C_baseline_failure_to_oa_legacy": {
    "scores": {
      "inflow": 39,
      "spending": 29,
      "competition": 29,
      "risk": 20
    },
    "footfall_provider": "oa21285",
    "spending_mode": "legacy_oa_spending_fallback",
    "competition_mode": "commercial_only",
    "tourism_status": "no_data",
    "data_insufficient": true
  },
  "D_realtime_failure_baseline_only": {
    "scores": {
      "inflow": 24,
      "spending": 31,
      "competition": 29,
      "risk": 20
    },
    "footfall_provider": "sdot",
    "spending_mode": "sales_baseline_only",
    "competition_mode": "commercial_only",
    "tourism_status": "no_data",
    "data_insufficient": true
  },
  "E_commercial_failure_legacy_sdot": {
    "scores": {
      "inflow": 24,
      "spending": 24,
      "competition": 34,
      "risk": 20
    },
    "footfall_provider": "sdot",
    "spending_mode": "legacy_oa_spending_fallback",
    "competition_mode": "legacy_sdot_fallback",
    "tourism_status": "no_data",
    "data_insufficient": true
  },
  "F_sdot_failure_commercial_only": {
    "scores": {
      "inflow": 24,
      "spending": 24,
      "competition": 29,
      "risk": 20
    },
    "footfall_provider": "sdot",
    "spending_mode": "legacy_oa_spending_fallback",
    "competition_mode": "commercial_only",
    "tourism_status": "no_data",
    "data_insufficient": true
  },
  "G_tourapi_failure_bonus_skip": {
    "scores": {
      "inflow": 24,
      "spending": 24,
      "competition": 29,
      "risk": 20
    },
    "footfall_provider": "sdot",
    "spending_mode": "legacy_oa_spending_fallback",
    "competition_mode": "commercial_only",
    "tourism_status": "no_data",
    "data_insufficient": true
  },
  "H_all_external_failure_feed_survives": {
    "scores": {
      "inflow": 24,
      "spending": 24,
      "competition": 18,
      "risk": 20
    },
    "footfall_provider": "sdot",
    "spending_mode": "legacy_oa_spending_fallback",
    "competition_mode": "base_only",
    "tourism_status": "no_data",
    "data_insufficient": true
  }
}
```

## 9. Deep-night / boundary

00:00·05:59는 직전 calendar day 저녁 20:00 대표값, 06:00부터 아침이다. 전체 경계는 심야 00–06 / 아침 06–12 / 점심 12–17 / 오후 17–20 / 저녁 20–24로 regression 검증했다. badge는 `심야 시간대는 데이터가 제한적입니다 (06시부터 갱신)`와 정확히 일치한다.

## 10. LLM semantics

점수·날씨 grade·rule tags는 IndicatorResult에서 고정되며 LLM은 설명 필드만 병합한다. 대표 7개구는 API key가 없어 `rule_fallback` 생성물을 검사했다. 정확한 방문객/매출, 개별 폐업 확률, KOSIS 자치구 위험, 업종별 경쟁률, 폐업필터 적용, 실시간 카드 전체 매출 주장은 없었다.

## 11. Structural risk deferred

flag=False, contribution=0, policy=deferred. disk artifact 존재와 무관하게 production risk에는 적용하지 않는다.

## 12. Blockers / warnings

- Reconstruction mismatch: 0
- Cap violation: 0
- Warnings: risk variance is near zero in stored-snapshot replay because weather/content are unavailable and structural risk is deferred
- Blockers: none

QA 중 weather `no_data`가 lexical default clear bonus를 받던 명확한 결측 처리 버그를 수정했다.

## 13. Production readiness

최종 판정은 **ready**. 저장 snapshot 기반 25개구 final feed 경로 성공, 재구성 mismatch 0, 심각한 saturation 없음, 명시적 fallback 및 structural deferred 정책이 유지됐다. 리스크 분산 0은 현재 replay의 결측 날씨/콘텐츠와 structural deferred에 따른 관측 경고이며 산식 변경 사유로 사용하지 않는다.
