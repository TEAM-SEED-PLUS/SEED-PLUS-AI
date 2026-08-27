# 상권날씨 Public Feed Schema v1

`schema_version: "1.0"`은 상권날씨 엔진이 다른 Backend에 전달하는 단일 public 계약이다. Backend는
`generate_public_market_feed(...)` 결과를 그대로 consume하면 된다. 기존 `generate_market_feed(...)`는 디버깅을 위한
internal result를 계속 반환한다.

## 전체 schema

```json
{
  "schema_version": "1.0",
  "query": {
    "district": "강남구",
    "date": "2026-08-23",
    "time": "18:00",
    "time_band": "오후"
  },
  "opportunity_score": 64,
  "market_weather": {
    "score": 64,
    "grade": "흐림",
    "emoji": "☁️"
  },
  "indicators": {
    "inflow_pressure": 60,
    "spending_intent": 55,
    "competition_pressure": 42,
    "operational_risk": 20
  },
  "decision_tags": ["관망 권장"],
  "narrative": {
    "generation_mode": "rule_fallback",
    "judgement_sentence": "현재 시간대에는 보수적 운영이 적합한 구간입니다.",
    "basis_sentence": "기회 점수와 네 지표를 종합했습니다.",
    "recommended_actions": ["액션 1", "액션 2", "액션 3"]
  },
  "data_quality": {
    "status": "ok",
    "data_insufficient": false,
    "badges": [],
    "fallback_sources": [],
    "no_data_sources": [],
    "stale_sources": [],
    "skipped_sources": [],
    "failed_sources": [],
    "empty_sources": [],
    "score_context": {"basis": "requested_time"}
  },
  "sources": {
    "weather": {"source": "weather", "status": "ok"},
    "content": {
      "festival": {"source": "festival", "status": "ok"},
      "event": {"source": "event", "status": "ok"},
      "performance": {"source": "performance", "status": "ok"},
      "sports": {"source": "sports", "status": "ok"}
    },
    "special_day": {"source": "special_day", "status": "ok"},
    "footfall": {"source": "oa21285", "status": "ok", "source_time": "2026-08-23T18:00:00+09:00", "snapshot_count": 5, "fallback": false},
    "tourism": {"source": "tourapi", "status": "ok", "requested_month": "202608", "source_month": "202608", "age_months": 0},
    "commercial_store": {"source": "commercial_store", "status": "complete", "mode": "commercial_plus_realtime", "requested_quarter": "2026Q3", "source_quarter": "2026Q3", "age_quarters": 0},
    "consumption_baseline": {"source": "OA-22176+OA-22173", "status": "ok", "requested_quarter": "2026Q3", "source_quarter": "2026Q3", "age_quarters": 0},
    "realtime_commerce": {"source": "oa22385", "status": "ok", "source_time": "20260823 1800", "age_minutes": 5, "valid_place_count": 82},
    "competition_sdot": {"source": "sdot", "status": "ok"}
  },
  "generated_at": "2026-08-23T18:05:00+09:00"
}
```

위 숫자와 시각은 schema 형태를 설명하는 예시이며 production snapshot을 주장하지 않는다.

## Field 설명

| Field | 의미 |
|---|---|
| `schema_version` | public 계약 버전. v1은 항상 `1.0`이다. |
| `query` | 정규화된 자치구·날짜·시각·확정 time band다. |
| `opportunity_score` | 실제 날씨 grade 계산 및 UI 표시에 사용되는 최종 기회 점수다. |
| `market_weather` | 동일 opportunity score, 확정 grade, emoji다. grade는 `맑음/구름/흐림/비/폭풍` 중 하나다. |
| `indicators` | rule engine이 확정한 네 점수다. |
| `decision_tags` | rule engine의 전체 확정 태그다. LLM이 변경하지 않는다. |
| `narrative` | 항상 존재하는 판단·근거·액션이다. 생성 방식만 `rule_fallback` 또는 `hybrid_llm`이다. |
| `data_quality.status` | 종합 상태: `ok/partial/fallback/no_data`다. |
| `data_quality.data_insufficient` | internal pipeline이 확정한 데이터 부족 여부다. |
| `badges` | 데이터 부족 및 심야 고지처럼 UI에 전달할 확정 문구다. |
| `fallback_sources` | 실제 대체 source 또는 이전 snapshot이 점수에 사용된 source다. |
| `no_data_sources` | 필요한 데이터를 확보하지 못한 source다. 정상적인 0건 결과는 제외한다. |
| `stale_sources` | snapshot은 있지만 허용 freshness를 초과해 점수에서 제외된 source다. |
| `skipped_sources` | 날짜·시간대·eligibility 불일치로 점수에서 제외된 source다. |
| `failed_sources` | API·파싱·처리 자체가 실패한 source다. |
| `empty_sources` | 조회는 성공했지만 해당 날짜·지역의 matching item이 0건인 source다. |
| `score_context` | 요청 시각 점수인지 심야 직전 저녁 reference인지 나타내는 기존 metadata다. |
| `sources` | UI에 필요한 source/provider, 상태, source 시점·월·분기와 age만 축약한다. |
| `generated_at` | public serialization 시각, Asia/Seoul ISO 8601이다. |

`raw_data`, `normalized_data`, contribution diagnostics, API 원문, exception detail, API key는 포함하지 않는다.
Historical/KOSIS structural risk도 production source로 노출하지 않는다.

## 정상 예시

정상 응답은 위 전체 schema와 같으며 `data_quality.status="ok"`, `data_insufficient=false`, 빈 badge/fallback 목록을 가진다.
모든 점수와 grade, tag는 LLM 실행 전에 rule engine이 확정한다.

## 데이터 부족 예시

```json
{
  "data_quality": {
    "status": "partial",
    "data_insufficient": true,
    "badges": ["일부 데이터가 부족해 대체값 또는 기본값을 사용했습니다"],
    "fallback_sources": ["footfall", "tourism", "realtime_commerce"],
    "score_context": {"basis": "requested_time"}
  },
  "sources": {
    "footfall": {"source": "sdot", "status": "no_data", "fallback": true},
    "tourism": {"source": "tourapi", "status": "no_data", "requested_month": "202608", "source_month": "202509", "age_months": 11},
    "realtime_commerce": {"source": "oa22385", "status": "no_data"}
  }
}
```

이는 전체 응답 중 관련 부분만 보인 예시다. 대체 source도 없으면 해당 bonus만 빠지고 base는 유지되며 cap/weight를
재분배하지 않는다.

## 심야 예시

```json
{
  "query": {"district": "강남구", "date": "2026-08-23", "time": "05:59", "time_band": "심야"},
  "data_quality": {
    "status": "partial",
    "data_insufficient": true,
    "badges": [
      "심야 시간대는 데이터가 제한적입니다 (06시부터 갱신)",
      "일부 데이터가 부족해 대체값 또는 기본값을 사용했습니다"
    ],
    "fallback_sources": ["tourism", "realtime_commerce"],
    "score_context": {
      "basis": "previous_evening_reference",
      "reference_date": "2026-08-22",
      "reference_time_band": "저녁",
      "reference_start": "20:00",
      "reference_end": "24:00",
      "representative_time": "20:00"
    }
  }
}
```

심야 점수는 현재 심야 실측이 아니라 직전 calendar day 저녁 reference다.

## OpenAI optional 예시

키가 없거나 호출이 실패하면 schema는 그대로이고 다음 값만 fallback을 나타낸다.

```json
{"narrative": {"generation_mode": "rule_fallback", "judgement_sentence": "...", "basis_sentence": "...", "recommended_actions": ["...", "...", "..."]}}
```

추후 `OPENAI_API_KEY`와 선택적으로 `OPENAI_MODEL` 환경변수를 설정하고 호출이 실제 성공하면 같은 schema에서 다음과 같다.

```json
{"narrative": {"generation_mode": "hybrid_llm", "judgement_sentence": "...", "basis_sentence": "...", "recommended_actions": ["...", "...", "..."]}}
```

LLM 성공 여부와 무관하게 scores, opportunity score, weather grade, decision tags, sources와 quality metadata는 바뀌지 않는다.
