# 홍대 주간 브리핑 API 및 발행 가이드

FE 및 Spring Boot BE는 FastAPI의 저장된 JSON을 조회합니다. 저장 파일 조회 중 서울시·SEMAS·OpenAI 호출은 발생하지 않습니다.

| 경로 | 파라미터 | 동작 |
| --- | --- | --- |
| `GET /api/v1/hongdae-weekly-briefing` | `start`, `end` 필수, YYYY-MM-DD | 지정 기간의 저장 브리핑. 미발행은 HTTP 404, 잘못된 날짜 및 역순은 422 |
| `GET /api/v1/hongdae-market-feed` | `date`, `time` 또는 `time_band` 선택, 서울 시간 | 기존 일별·시간대 분석. `opportunity_score` 유지 |

기존 `/api/v1/weather-feeds`와 응답 필드는 유지됩니다. 주간 응답의 기존 `period`, `weekly_average_score`, `week_over_week`, `daily_scores`, `structural_baseline`, `completeness`, `data_quality`, `narrative` 등은 그대로 제공됩니다. 세 신규 영역은 추가 필드입니다. 재생성 전 구형 저장본에서는 신규 영역이 null일 수 있습니다.

| 신규 객체 | 필드와 의미 |
| --- | --- |
| `weekly_vitality` | `analysis_period`, `comparison_period`는 start/end 문자열 객체. `daily_population`, `matches`, `excluded_bands`는 배열. `matched_bands`, `expected_bands`는 개수. `population_change_pct`는 구간별 변화율의 평균, `matched_mean_population_change_pct`는 구간 평균 인구의 변화율. `full_week_population_change_pct`는 완전한 두 주의 시간별 평균 변화율. 활발·약세 날짜와 시간대는 `most_active_population_day`, `quietest_population_day`, `strongest_population_band`, `weakest_population_band` |
| `structural_environment` | 행정동명·코드, `source_period`, `source_reference_date`, 원 단위 `estimated_sales`, `sales_transaction_count`, `sales_percentile`, `sales_percentile_population`, 전체·일반·프랜차이즈 점포수, 개폐업 점포수와 백분율, 업종 코드별 `industries`, 출처와 시점 `metadata` |
| `nearby_competition` | `latitude`, `longitude`, 미터 단위 `radius_m`, `source_updated_at`, `total_store_count`, km²당 `stores_per_km2`, 업종별 수와 백분율, `source`, `source_file`, `method`, `source_status`, `usage` |

`weekly_vitality.status`는 전 구간 비교가 가능한 `complete`, 일부 구간만 비교 가능한 `partial`, 분석 기간 관측이 없는 `no_data`로 구분합니다. 기존 최상위 `completeness`는 분석 주간의 `complete/partial` 계약을 유지합니다. 관측·분모·기준선이 없으면 해당 수치는 null입니다. 7일 전체가 비교 불가능하면 `week_over_week`와 `full_week_population_change_pct`는 null입니다. 비교할 수 있는 구간은 별도 지표로 남습니다.

실제 예시의 일부입니다. [11/16~22 전체 JSON](../data/hongdae/weekly/2026-11-16_2026-11-22.json)과 [11/09~15 전체 JSON](../data/hongdae/weekly/2026-11-09_2026-11-15.json)에 배열과 기준선 상세값이 있습니다.

```json
{
  "period": {"start": "2026-11-16", "end": "2026-11-22"},
  "weekly_average_score": 62.0,
  "week_over_week": null,
  "completeness": "partial",
  "weekly_vitality": {
    "status": "partial",
    "matched_bands": 27,
    "expected_bands": 35,
    "population_change_pct": 0.17,
    "matched_mean_population_change_pct": 0.91,
    "full_week_population_change_pct": null
  },
  "structural_environment": {
    "administrative_dong": "서교동",
    "source_period": "2026Q2",
    "sales_percentile": 98.24,
    "sales_percentile_population": 425,
    "total_store_count": 8783
  },
  "nearby_competition": {
    "radius_m": 300,
    "source_updated_at": "2026-09-30",
    "total_store_count": 2239
  }
}
```

생활인구 출처는 서울특별시·KT, 서울시 통신데이터 ([OA-22784](https://data.seoul.go.kr/dataList/OA-22784/S/1/datasetView.do))입니다. 시점별 반경 추정 인구이며 비식별값은 보존하고 해당 비교 구간을 제외합니다. 분기 원천은 서울시 [OA-22175](https://data.seoul.go.kr/dataList/OA-22175/S/1/datasetView.do)와 [OA-22172](https://data.seoul.go.kr/dataList/OA-22172/S/1/datasetView.do)입니다. 매출 백분위는 동분기 서울 행정동 매출 규모의 동점 중간 순위입니다. 주변 경쟁은 [SEMAS 상가정보](https://www.data.go.kr/data/15083033/fileData.do)의 기준일 스냅샷입니다.

## 발행 및 재발행

```bash
python3 -m hongdae_jobs population --start 2026-11-16
python3 -m hongdae_jobs daily --start 2026-11-09 --days 14
python3 -m hongdae_jobs weekly --start 2026-11-16
# 수집 → 일별 계산 → 주간 생성, --start 생략 시 지난주 월요일
python3 -m hongdae_jobs publish
# 공지된 수정본 강제 확인과 해당 주 재발행
python3 -m hongdae_jobs publish --start 2026-11-16 --refresh 2026-11-21
```

FastAPI lifespan이 내부 스케줄러를 실행합니다. 서울 시간 매일 15:30에 생활인구 D−4 자료를 확인하고, 변경된 날짜를 일별 분석합니다. 목요일 15:30에는 수집과 계산 후 지난주 월~일을 발행합니다. 기존 발행 기간 또는 비교 기간에 데이터가 보충되면 해당 브리핑도 갱신하고, 입력 해시가 같으면 발행을 생략합니다. 자동 발행은 결정론적 요약을 사용합니다.

수집 실패일은 manifest의 `retry_dates`에 남고 최근 최대 7개를 다음 수집에서 확인합니다. 공식 목록의 식별자·수정일·크기가 바뀌거나 `--refresh`를 요청하면 다시 검증하고 해시가 바뀐 파일만 원자적으로 교체합니다. 자동 수집이 닿지 않는 과거 수정본은 `--refresh` 또는 수동 ZIP 적재를 사용합니다. 수동 CLI는 장애 복구나 즉시 재발행에 사용할 수 있습니다.

날씨 overview는 코드 상수로 기본 활성화되며 5분 간격, 7일 보존입니다. 해당 삭제 정책은 날씨 overview 저장 경로에만 적용됩니다. 운영은 기존 Compose의 단일 컨테이너와 단일 Uvicorn worker를 사용합니다. 앱 스케줄러와 홍대 수동 CLI는 기존 `./data:/app/data` bind mount의 파일 잠금을 공유합니다. 초기 적재 목록, 로그와 복구 절차는 [배포 가이드](hongdae-operations.md)를 참고하세요.

일별 저장본은 관측일, 수집 시각(수동 원본은 확인할 수 없으면 null), 계산 시각과 입력 해시를 구분합니다. ZIP 수집에는 HTTPS 공개 다운로드를 사용합니다. 서울시 평문 OpenAPI는 인증키가 URL에 들어가므로 생활인구 자동 수집에 사용하지 않습니다. `--llm`은 생성 시에만 선택적으로 사용하며 실패 시 결정론적 요약을 저장합니다.
