# 경쟁 압박 실측 점포수 API 조사 (2026-08-23)

이 문서는 조사 결과이며 production competition 산식을 변경하지 않는다. 국세청 API, 폐업 필터,
업종 매핑표, 점수 변환식도 도입하지 않았다.

## 1. 결론

- `storeListInRadius`, `storeListInDong`, `storeListInUpjong`,
  `storeListInRectangle`, `storeListInPolygon`을 HTTPS live 호출로 확인했다.
- 시군구 코드(`divId=signguCd`)로 서울 자치구 전체를 직접 조회할 수 있다.
- 자치구 조회에 `indsLclsCd`, `indsMclsCd`, `indsSclsCd`를 함께 주면 지역+업종 집계도
  서버에서 직접 가능하다.
- 최대 실효 `numOfRows`는 1,000이다. 5,000/10,000을 요청해도 1,000건만 반환했다.
- 점포 레코드의 안정적인 dedupe key는 `bizesId`(상가업소번호)다.
- 사업자등록번호, 폐업 여부, 레코드 수정일/기준일은 점포 응답에 없다.
  `business_registration_number_available=false`이다.
- 현재 weatherfeed 추천 업종은 콘텐츠/날씨 규칙이 만드는 자연어
  `recommended_categories: list[str]`이고 입력에는 업종이 없다. API의 코드 taxonomy와 직접
  일치하지 않으므로 mapping이 필요하다.
- production 교체안은 후보 B(실측 점포 경쟁도를 주 component로, S-DoT spike를 작은 실시간
  보정으로 유지)를 추천한다. 다만 공식 지표·공간단위·업종 매핑·정규화가 결정되기 전에는
  현 산식을 유지한다.

## 2. 현재 competition과 입력/출력

현재 식은 다음과 같다.

```text
competition_pressure = clamp(
  18
  + min(25, sports*6 + festival*3.5 + all_content_items*1.3)
  + min(16, max(0, sdot_visitor_spike_ratio-1)*9)
)
```

- base: 18
- content contribution cap: 25
- S-DoT spike contribution cap: 16
- 최종 competition cap: 공통 `clamp`에 의해 100
- OA-21285 사용 시에도 OA temporal spike는 semantic scale 불일치로 competition에 쓰지 않고,
  별도 `competition_footfall`의 S-DoT spike를 쓴다.
- request query: `district`, `date`, `time` (업종 없음)
- 추천 업종 output: `recommended_categories` (예: `음식점`, `카페`, `간편식`, `배달·포장`)

## 3. live operation과 parameter

Base URL: `https://apis.data.go.kr/B553077/api/open/sdsc2`

| 기능 | operation | live 결과 | 핵심 parameter |
|---|---|---:|---|
| 반경 점포 | `storeListInRadius` | 성공 | `cx`(경도), `cy`(위도), `radius`, pagination |
| 행정구역 점포 | `storeListInDong` | 성공 | `divId=ctprvnCd/signguCd/adongCd`, `key=행정코드` |
| 업종 점포 | `storeListInUpjong` | 성공 | `divId=indsLclsCd/indsMclsCd/indsSclsCd`, `key=업종코드` |
| 행정구역+업종 | `storeListInDong` | 성공 | 위 행정 parameter + `indsLclsCd/MclsCd/SclsCd` |
| 사각형 점포 | `storeListInRectangle` | 성공 | `minx`, `miny`, `maxx`, `maxy` |
| 다각형 점포 | `storeListInPolygon` | 성공 | `key=POLYGON(...)` |
| 지정 상권 점포 | `storeListInArea` | operation 정상 | `key=상권번호`; 임의 polygon용 operation이 아님 |
| 업종 목록 | `largeUpjongList`, `middleUpjongList`, `smallUpjongList` | 성공 | live에서는 전체 목록 반환 |
| 행정구역 내 상권 | `storeZoneInAdmi` | 성공 | `divId`, `key` |

공통 parameter는 `serviceKey`, `pageNo`, `numOfRows`, `type=json`이다. 강남역 중심
`(cx=127.0276, cy=37.4979, radius=500)` 예시는 6,615건이었다. 같은 인접 사각형/다각형
예시는 각각 5,409건으로 성공했다. radius는 API 기능상 지원되지만 service의 공식 radius는
아직 정하지 않는다.

## 4. 실제 점포 schema

| 의미 | 실제 필드 |
|---|---|
| 상가업소번호/고유 ID | `bizesId` |
| 상호명/지점명 | `bizesNm`, `brchNm` |
| 대분류 코드/명 | `indsLclsCd`, `indsLclsNm` |
| 중분류 코드/명 | `indsMclsCd`, `indsMclsNm` |
| 소분류 코드/명 | `indsSclsCd`, `indsSclsNm` |
| 표준산업분류 | `ksicCd`, `ksicNm` |
| 시도 | `ctprvnCd`, `ctprvnNm` |
| 시군구 | `signguCd`, `signguNm` |
| 행정동 | `adongCd`, `adongNm` |
| 법정동 | `ldongCd`, `ldongNm` |
| 지번/도로명 주소 | `lnoAdr`, `rdnmAdr` |
| 경도/위도 | `lon`, `lat` |

그 밖에 `lnoCd`, `rdnmCd`, `bldMngNo`, 건물/층/호/우편번호 필드가 있다. 사업자등록번호와
폐업 상태 필드는 없다. `bizesId`/`bizesNm`은 각각 상가업소 ID/상호명이며 사업자등록번호가
아니다.

## 5. 서울 25개구 live totalCount

각 구는 `numOfRows=1` 한 번으로 `totalCount`만 얻었다. 25회 호출이면 lightweight batch가
가능하다.

| 자치구 | 점포 수 | 자치구 | 점포 수 |
|---|---:|---|---:|
| 강남구 | 66,269 | 강동구 | 19,748 |
| 강북구 | 12,604 | 강서구 | 27,098 |
| 관악구 | 20,325 | 광진구 | 18,877 |
| 구로구 | 18,559 | 금천구 | 18,353 |
| 노원구 | 15,686 | 도봉구 | 10,610 |
| 동대문구 | 17,303 | 동작구 | 14,187 |
| 마포구 | 32,102 | 서대문구 | 14,152 |
| 서초구 | 40,115 | 성동구 | 17,782 |
| 성북구 | 15,889 | 송파구 | 34,860 |
| 양천구 | 16,015 | 영등포구 | 29,425 |
| 용산구 | 16,301 | 은평구 | 16,363 |
| 종로구 | 21,616 | 중구 | 24,801 |
| 중랑구 | 15,052 |  |  |

합계 554,092, 평균 22,163.68, 중앙값 18,353, 모집단 표준편차 11,476.25, 최솟값
10,610(도봉구), 최댓값 66,269(강남구)다. 강남이 평균의 약 2.99배로 총점포수는 큰 구에
상당히 치우치므로 raw total을 점수로 바로 쓰면 안 된다.

## 6. 3개구 전체 pagination과 데이터 품질

| 구 | total/unique | API 호출 수 | 페이지 중복 ID | 좌표 null | 업종 null | 구 null |
|---|---:|---:|---:|---:|---:|---:|
| 강남구 | 66,269 | 67 | 0 | 0 | 0 | 0 |
| 종로구 | 21,616 | 22 | 0 | 0 | 0 | 0 |
| 마포구 | 32,102 | 33 | 0 | 0 | 0 | 0 |

세 구 전체 120개 호출, 119,987개 레코드에서 동일 `bizesId` 중복은 없었다. 여러 조건
결과를 합칠 때는 같은 점포가 당연히 겹칠 수 있으므로 항상 `bizesId`로 dedupe한다.
상호명+주소는 우선 키로 쓰지 않는다. 전체 25개구 full snapshot 예상 호출량은 현재 count
기준 `sum(ceil(count/1000)) = 568`회다.

## 7. taxonomy와 업종별 분포

실제 레코드 예시는 다음과 같다.

- `I2 음식 > I212 비알코올 > I21201 카페`
- `I2 음식 > I201 한식 > I20101 백반/한정식`
- `M1 과학·기술 > M107 본사·경영 컨설팅 > M10703 경영 컨설팅업`
- `S2 수리·개인 > S207 이용·미용 > S20701 미용실`

live taxonomy 목록은 대 25, 중 266, 소 1,255개를 반환하고 `stdrDt=2023-02-28`을
표시했다. 반면 현재 공공데이터포털 설명은 개편 taxonomy 10/75/247 및 분기 업데이트를
명시한다. 즉 live 목록 endpoint/기준일과 최신 포털 설명 사이에 불일치가 있어 production
mapping 전에 제공기관 확인과 실제 snapshot taxonomy 고정이 필요하다.

| 실제 소분류 | 강남 | 종로 | 마포 |
|---|---:|---:|---:|
| `I21201` 카페 | 2,123 | 1,248 | 1,852 |
| `I20101` 백반/한정식 | 2,699 | 1,450 | 1,564 |
| `M10703` 경영 컨설팅업 | 6,592 | 731 | 1,147 |

같은 업종 수는 구별 차이를 충분히 만들지만 구 전체 크기도 함께 반영하므로 count만으로
경쟁을 단정할 수 없다.

```json
{
  "commercial_api_taxonomy": "indsLclsCd/Nm > indsMclsCd/Nm > indsSclsCd/Nm (live 25/266/1255; portal description 10/75/247)",
  "weatherfeed_taxonomy": "content/weather-derived recommended_categories natural-language labels",
  "direct_mapping_possible": false,
  "mapping_required": true
}
```

## 8. 계산 가능한 후보 지표

| 지표 | 계산 가능 | 장점 | 주의점 |
|---|---|---|---|
| `district_total_store_count` | 예 | 단순, 1회 count | 구 면적/상업 중심성 영향이 큼 |
| `district_same_industry_store_count` | 예 | 직접 경쟁 후보 | 추천 업종→API code mapping 필요 |
| `same_industry_ratio` | 예 | 구 크기 일부 보정 | 소수 업종/복합 업종 해석 필요 |
| `stores_per_area` | 조건부 | 공간 규모 보정 | 신뢰할 자치구/상권 면적 source 필요 |
| `stores_in_radius` | 예 | 실제 생활권에 가까움 | 중심점과 radius를 기획 결정해야 함 |

official competition은 아직 선택하지 않는다. 특히 사용자의 업종 입력이 없기 때문에
`same_industry`의 기준 자체가 현재 request에서 정의되지 않는다.

## 9. 기존 proxy 교체 후보

- 후보 A: S-DoT spike 완전 제거. “proxy → 실측” 문구에는 가장 직접적이나 실시간 과열
  신호를 모두 잃는다.
- 후보 B(추천): 실측 점포 경쟁도를 주 component로 두고 S-DoT spike는 작은 실시간 보정으로
  유지한다. 점포 재고(stock)와 순간 혼잡(flow)의 의미를 분리할 수 있다.
- 후보 C: 둘을 완전히 별도 component로 유지. 설명력은 높지만 현재 단일 competition 축과
  opportunity weight 계약을 바꾸는 범위가 커진다.

기획서의 핵심은 현재 S-DoT 혼잡 proxy를 점포 실측으로 대체하는 것이므로 B가 현실적인
목표 상태다. content contribution은 경쟁업체 실측이 아니라 이벤트성 수요/혼잡 신호이므로
별도 재검토가 필요하며 자동으로 삭제할 대상은 아니다.

## 10. source status와 batch/cache 제안

- `ok`: 기대한 모든 page 수집, unique count가 totalCount와 일치
- `partial`: 일부 page 실패 또는 unique count 불일치
- `no_data`: API `NODATA_ERROR`/정상 0건
- `failed`: 인증/transport/API 오류로 usable snapshot 없음
- `fallback`: 직전 정상 snapshot을 허용 기간 안에서 사용

포털 파일데이터는 분기 업데이트로 표시된다. 요청마다 568회 full batch를 호출하지 말고
분기 snapshot을 기본으로 하되 월 1회 또는 제공기관 공지/기준일 변경 시 갱신을 권장한다.

```text
data/commercial_stores/
  raw/YYYYMMDD/{district_code}/page-NNN.json
  normalized/YYYYMMDD/stores.jsonl
  aggregates/YYYYMMDD/district_category_counts.json
  manifest.json
```

manifest에는 source URL(키 제외), 수집시각, taxonomy 기준일, total/page/unique count, checksum,
status, fallback origin을 기록한다. raw 응답에는 인증키나 request URL을 저장하지 않는다.

## 11. production 전에 필요한 기획 결정

1. competition 공간단위: 자치구 / 중심점 radius / 상권 polygon
2. 비교 업종: 사용자 입력 도입 여부, 추천 업종 사용 여부, 대·중·소분류 수준
3. weatherfeed 자연어 추천 업종과 API 코드의 검수된 mapping 및 unmapped 정책
4. 절대 점포수 / 업종 비율 / 면적 밀도 중 공식 지표
5. 서울 내 percentile, log 등 정규화 모집단·기준일·cap (이번 조사에서 확정하지 않음)
6. S-DoT 보정의 잔존 여부/비중과 content competition의 의미
7. snapshot 허용 수명, partial/fallback 정책
8. live taxonomy와 포털 설명 불일치 처리 및 상가업소번호 개편 시 snapshot 간 ID 연계 정책

## 12. 변경 파일과 테스트

- `commercial_store_api.py`: 조사 전용 client, pagination, `bizesId` dedupe, status, key 비노출
- `tests/test_commercial_store_api.py`: 정상/no_data/error/pagination/dedupe/null/key 비노출 mock 테스트
- `docs/commercial_store_probe_20260823.md`: 이 조사 보고서

`indicator_engine.py`, `integrated_city_info.py`, `normalized_city_data.py`, `common.py`,
`v1_config.py`의 production 동작은 변경하지 않았다.
