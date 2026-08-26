"""Official market-weather v1 scoring and decision constants."""

OPPORTUNITY_WEIGHTS = {
    "inflow": 0.35,
    "spending": 0.30,
    "inverse_competition": 0.15,
    "inverse_risk": 0.20,
}

WEATHER_THRESHOLDS = {
    "clear": 80,
    "cloud": 65,
    "overcast": 50,
    "rain": 35,
}

TAG_THRESHOLDS = {
    "entry_opportunity": 65,
    "entry_competition_max_exclusive": 65,
    "entry_risk_max_exclusive": 55,
    "inflow_opportunity": 68,
    "spending_opportunity": 62,
    "overheated_competition": 70,
    "risk_caution": 55,
    "category_spread": 30,
    "category_content_count": 2,
}

SOURCE_STATUSES = {"ok", "no_data", "fallback", "failed"}
NIGHT_NOTICE = "심야 시간대는 데이터가 제한적입니다 (06시부터 갱신)"
REFERENCE_EVENING_TIME = "20:00"

# Investigated and validated, but deliberately deferred from production.  The
# official Seoul OA-22172/OA-22173 lifecycle ended without a confirmed refresh
# replacement; never auto-enable this flag from snapshot availability.
STRUCTURAL_CLOSURE_RISK_ENABLED = False
STRUCTURAL_CLOSURE_RISK_POLICY = {
    "status": "deferred",
    "production_applied": False,
    "recommended_metric": "recent_4_complete_quarters_pooled_closure_rate_percentile",
    "recommended_base_structure": "fixed10_plus_structural_max10",
    "reason_codes": (
        "no_current_official_district_refresh_source",
        "kosis_no_seoul_district_grain",
        "legacy_seoul_dataset_discontinued",
    ),
}

# Provider-independent footfall contribution caps. S-DoT and OA adapters share
# these limits while using different level normalization formulas.
FOOTFALL_AVG_INFLOW_CAP = 22.0
FOOTFALL_MAX_INFLOW_CAP = 8.0
FOOTFALL_AVG_SPENDING_CAP = 10.0
FOOTFALL_COMPETITION_CAP = 16.0
FOOTFALL_SPIKE_MULTIPLIER = 9.0

# Official v1 measured competition policy.  The two components preserve the
# previous aggregate footfall competition cap of 16.
COMMERCIAL_STORE_COMPETITION_ENABLED = True
COMMERCIAL_STORE_COMPETITION_CAP = 12.0
SDOT_REALTIME_COMPETITION_CAP = 4.0

# Measured consumption reuses the former max-10 footfall spending budget.
# Activated after aligned snapshots, mapping, fallback, regression, and
# distribution validation completed on 2026-08-23.
CONSUMPTION_HYBRID_ENABLED = True
SALES_BASELINE_SPENDING_CAP = 7.0
REALTIME_COMMERCE_SPENDING_CAP = 3.0

# TourAPI v1 production scoring policy.
TOURISM_SCORING_ENABLED = True
TOURISM_INFLOW_BONUS_CAP = 10.0
TOURISM_SPENDING_BONUS_CAP = 10.0
MAX_TOURISM_FALLBACK_MONTHS = 3
TOURISM_V1_COMPONENT_POLICY = {
    "stay": ("2101", "2102", "2103", "2104", "2105"),
    "foreign_visitor": ("3302",),
    "culture": ("1201", "1202", "1203", "1204", "1205"),
    "spending": ("2201", "2202", "2203"),
    "food": ("1106",),
    "age_spending_diversity": ("3201", "3202", "3203", "3204", "3205", "3206", "3207"),
}
TOURISM_V1_INFLOW_WEIGHTS = {"stay": 0.50, "foreign_visitor": 0.30, "culture": 0.20}
TOURISM_V1_SPENDING_WEIGHTS = {"spending": 0.50, "food": 0.35, "age_spending_diversity": 0.15}
