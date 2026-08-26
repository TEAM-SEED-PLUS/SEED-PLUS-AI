# [F] KOSIS 운영리스크 연동 조사 (2026-08-23)

## 결론

production risk는 변경하지 않았다. 공식 `기업생멸행정통계`는 `소멸률(%)`을 직접 제공하지만, 확인된 지역 grain은 시도이고 서울 25개 자치구 값은 없다. 따라서 현재 확보 자료만으로 후보 B(자치구별 baseline)와 C(자치구×산업 가중평균)는 구현할 수 없다. 서울 전체 2023년 값은 모든 구에 같은 상수이므로 자치구 실측치로 표현해서는 안 된다.

현재 환경에 `KOSIS_API_KEY`가 없어 OpenAPI live response까지 검증하지 못했다. 공식 공표 PDF의 실제 값을 snapshot에 보존했고, 조사 client는 production pipeline과 분리했다.

## 0. 현재 production risk

- `indicator_engine.calculate_indicators`: `clamp(20 + content + weather + special_day)`
- base: 20
- content: `min(12, outdoor_hits×1.7 + sports_count×1.8 + festival_count×1.3)`
- weather: 흐림 +6, 비 +16, 폭풍 +28; 비×야외 콘텐츠 `min(8, hits×1.4)`; 강수 +3/+8, 바람 +4/+8, 극단기온 +10. weather 전체 합산 cap은 없고 최종 risk clamp가 적용된다.
- special day: `min(8, holiday_count×3)`
- 최종 clamp: 0~100
- 실제 폐업·사업체 데이터: 없음. 상가업소 수는 competition에만 사용된다.
- 방향: 높을수록 위험. `risk >= 55`에서 리스크 주의, opportunity에는 `(100-risk)×0.20`으로 반영된다.
- `market_feed_pipeline`, `normalized_city_data`, `llm_feed_writer`에도 KOSIS risk 입력은 없다.

## 1. 식별한 공식 통계표

| ID | 표명 | 기관 | 주기 | 확인 지표/단위 | 최신 소멸 기준연도 | grain |
|---|---|---|---|---|---:|---|
| `DT_1BD1001` | 산업별 기업수(활동/신생/소멸) | 국가데이터처(기관코드 101) | 연간 | 활동·신생·소멸기업 수(개), 신생률·소멸률(%) | 2023 잠정 | 전국 × KSIC 제10차 대/중분류 |
| `DT_1BD1007` | 지역별 기업수(활동/신생/소멸) | 국가데이터처(101) | 연간 | 활동·신생·소멸기업 수(개) | 2023 잠정 | 전국/17 시도; 공표표는 산업중분류 상세를 KOSIS로 안내 |

2025-10-23에 공표된 “2024년 기업생멸행정통계(잠정)”에서 활동·신생은 2024년, 소멸은 판별 시차 때문에 2023년이다. 현 시점(2026) `requested_year=2026`, `source_year=2023`, `age_years=3`, `source_status=publication_verified_api_not_probed`로 기록한다. 향후 분류 개편 시 제11차 KSIC 전환 여부도 snapshot 단위로 고정해야 한다.

공식 정의는 `소멸률 = 당해 연도(t-1년) 활동기업에 대한 소멸기업의 비율`이다. 폐업신고가 없어도 매출과 상용근로자가 1년 이상 없으면 소멸로 판별될 수 있으므로, 이를 세무상 폐업률 또는 개별 점포 폐업확률이라고 부르지 않는다.

## 2. 직접 제공과 지역 grain

산업 표에는 소멸률(%)이 직접 있다. 전산업 10.5%, 숙박·음식점업 14.7% 등이므로 임의 비율 대신 직접 값을 우선한다. 지역 표는 같은 2023년 활동기업 수와 소멸기업 수를 제공해 동일 모집단 비율 계산은 기술적으로 가능하지만, 공표표 자체의 제공 단위는 개이고 본 조사에서는 production metric으로 채택하지 않았다.

```json
{
  "district_level_available": false,
  "available_region_level": "시도",
  "mapping_required": true
}
```

강남·종로·마포·송파·금천·강서 sample은 공식 표에 없으므로 숫자를 만들지 않았다. 서울 시도 sample은 활동기업 1,530,152개, 소멸기업 156,148개(모두 2023)다. 전국은 활동 7,538,736개 대비 소멸 790,540개이며 공식 직접 소멸률은 10.5%다.

## 3. 산업 taxonomy와 실제 sample

```json
{
  "kosis_taxonomy": "한국표준산업분류 제10차, 대분류와 중분류",
  "weatherfeed_taxonomy": "콘텐츠 근거에서 생성되는 자유문자열 recommended_categories",
  "direct_mapping_possible": false,
  "mapping_required": true
}
```

대표 direct rate: 제조업 6.8%, 도·소매업 13.0%, 숙박·음식점업 14.7%, 정보통신업 11.0%, 보건·사회복지 3.2%, 예술·스포츠·여가 13.5%. `recommended_categories`는 계산 결과이므로 risk 입력으로 되먹이지 않는다. 향후 사용하려면 사용자 업종 입력과 검수된 explicit KSIC mapping이 필요하며 LLM 추측 mapping은 금지한다.

## 4. 후보와 분포

- A 서울 전체 연간 baseline: 가능하지만 25개구에 동일한 값이라 지역 차별성이 0이다.
- B 자치구별 전체 소멸률: 현재 확인 자료로 불가능.
- C 자치구×산업 구성비 가중평균: 자치구 값과 승인된 구성비·taxonomy가 없어 불가능.
- D 업종별 설명 신호: 향후 명시적 업종 입력이 생기면 가능. 현재 production에는 미사용이 안전하다.

KSIC 대분류 18개의 직접 소멸률 분포는 mean 9.7111, median 9.85, population std 3.4016, min 2.8, max 14.7이다. 산업간 범위가 크며 숙박·음식점업이 97.22 percentile/min-max 10, 전기·가스·증기가 2.78 percentile/min-max 0이다. min-max는 양 끝 산업에 민감하고 중간 값을 압축한다. percentile은 outlier 크기의 영향을 줄이지만 표본 18개의 계단형 순위이고, 업종 입력 없는 v1에서는 어느 산업 percentile을 선택할 근거가 없다. z-score도 동일한 문제를 해결하지 못한다.

참고 진단으로, 동일 grain의 시도별 2023 활동/소멸 수를 공식 정의대로 나눈 17개 시도 파생률은 mean 10.5610, median 10.5457, std 0.6489, min 9.7444, max 12.1258다. 서울은 10.2047%다. 이는 자치구 분포가 아니며 direct KOSIS rate로 표시하지 않는다.

## 5. production 후보와 fallback

KOSIS는 연간 구조적 사업 위험, 기존 content/weather/special-day는 단기 운영 위험으로 의미를 분리할 수 있다. “프록시 → 실측 교체”와 기존 총점 budget을 고려하면, 충분한 지역 grain이 확보될 때 후보 B인 `base20의 일부를 structural baseline으로 교체`하는 구조가 가장 적절하다. 신규 bonus 추가는 총 risk budget을 키우므로 우선순위가 낮고, 기존 단기 contribution은 KOSIS와 의미가 달라 교체 대상이 아니다. 이번 단계에서는 교체 폭과 cap을 정하지 않았다.

fallback 후보:

- 정상·허용 연령 snapshot: 승인된 structural baseline 사용
- snapshot 없음/API 실패: 기존 `base20 + content + weather + special-day` 그대로 유지
- 오래된 snapshot: 값을 재정규화하지 않고 source year/age/status 표시
- 모든 결측: 남은 contribution 재정규화 금지

LLM에는 향후 `연간 공식 통계 기반 구조적 영업 위험`으로만 전달하며 실시간 위험이나 개별 사업장의 폐업확률로 표현하지 않는다.

## 6. 구현 전 필요한 기획 결정

1. 서울 25개구 직접 통계 또는 동일 모집단 분자·분모의 별도 공식 자료를 확보할지
2. 구별 값이 없을 때 차별성 없는 서울 상수를 실제로 base에 넣을지
3. 허용 source age와 잠정치→확정치 교체 정책
4. 사용자 업종 입력 및 검수된 KSIC mapping을 도입할지
5. base20 중 교체 budget과 normalization 기준(서울 25구 percentile을 만들 수 있을 때 재검토)

## 7. OpenAPI와 snapshot

조사 client는 `KOSIS_API_KEY`만 환경변수에서 읽고 key를 메시지·결과에 포함하지 않는다. timeout, bounded retry, 빈 배열 `no_data`, 오류 dict `failed exception`, malformed response, grain dedupe, 연도·지역·산업 parsing을 지원한다. endpoint는 `statisticsParameterData.do?method=getList`이며 사용자 요청마다 호출하지 않고 `data/risk/kosis/{raw,normalized}` 연간 snapshot을 갱신하는 용도다.

현재 raw 파일은 API 원문이 아니라 공식 공표자료 전사본임을 명시했다. API key가 준비되면 두 표의 실제 `objL*`, `itmId`, 최신 table schema를 live response로 재확인한 뒤에만 raw API snapshot으로 승격한다.
