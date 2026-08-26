# OA-22173 자치구 폐업률 calibration preview

## 결론

2026Q1 OA-22173는 자치구 구조적 운영리스크 후보로 사용할 수 있다. `CLSBIZ_RT`는 공식 폐업률이며, 본 preview의 자치구 폐업률은 서비스업종별 비율의 단순 평균이 아니라 `Σ 폐업점포 / Σ 전체점포 × 100`으로 계산했다. production risk, base20, feature flag는 변경하지 않았다.

다만 로컬에 complete snapshot이 2026Q1 한 개뿐이고 OA-22173 공식 페이지는 현재 서비스 종료로 표시된다. 따라서 최근 4분기 안정성·순위 변동 검증과 지속 가능한 refresh 경로가 production 연결 blocker다.

## 데이터 정합성

- 재사용 원본: `data/consumption/baseline/oa22173_raw/2026Q1.jsonl`
- 2,495행, 서울 25개구, 서비스업종 코드 100개
- grain: 분기 × 자치구 × 서비스업종 코드
- 중복 grain: 0
- 서비스업종 코드 다중 명칭: 0
- 자치구별 업종 행: 99~100개; 없는 조합을 0으로 보정하지 않음
- `SIMILR_INDUTY_STOR_CO = STOR_CO + FRC_STOR_CO` 불일치: 0행
- 구조상 각 행은 단일 서비스업종 코드이며 중복 모집단 징후는 확인되지 않았다. 개별 점포 ID가 없는 집계표이므로 점포 수준 중복을 독립적으로 재식별한 것은 아니다.

공식 필드 의미는 `CLSBIZ_RT=폐업률`, `CLSBIZ_STOR_CO=폐업점포 수`, `SIMILR_INDUTY_STOR_CO=전체점포 수`다. 서울시 설명대로 전체점포는 일반점포+프랜차이즈점포다.

행별 `폐업점포 수 / 전체점포 수 × 100` 검증:

- 유효 행: 2,495, zero denominator: 0
- exact match: 692행, 27.7355%
- 반올림 허용오차 0.051%p 이내: 2,495행, 100%
- 평균 절대 차이: 0.018749%p
- 최대 차이: 0.05%p
- mismatch: 0행

## 2026Q1 서울 25개구

| 자치구 | 전체점포 | 폐업점포 | 폐업률 % | Percentile |
|---|---:|---:|---:|---:|
| 종로구 | 24,775 | 567 | 2.2886 | 8.33 |
| 중구 | 37,788 | 819 | 2.1674 | 0.00 |
| 용산구 | 19,698 | 453 | 2.2997 | 12.50 |
| 성동구 | 20,520 | 528 | 2.5731 | 29.17 |
| 광진구 | 20,425 | 536 | 2.6242 | 37.50 |
| 동대문구 | 21,621 | 589 | 2.7242 | 45.83 |
| 중랑구 | 17,687 | 539 | 3.0474 | 79.17 |
| 성북구 | 18,661 | 552 | 2.9580 | 70.83 |
| 강북구 | 13,625 | 445 | 3.2661 | 95.83 |
| 도봉구 | 13,101 | 386 | 2.9463 | 66.67 |
| 노원구 | 20,268 | 569 | 2.8074 | 54.17 |
| 은평구 | 19,610 | 604 | 3.0801 | 87.50 |
| 서대문구 | 15,303 | 385 | 2.5158 | 25.00 |
| 마포구 | 32,366 | 1,030 | 3.1824 | 91.67 |
| 양천구 | 19,939 | 580 | 2.9089 | 62.50 |
| 강서구 | 31,341 | 962 | 3.0695 | 83.33 |
| 구로구 | 24,480 | 582 | 2.3775 | 16.67 |
| 금천구 | 18,613 | 479 | 2.5735 | 33.33 |
| 영등포구 | 29,658 | 781 | 2.6334 | 41.67 |
| 동작구 | 16,693 | 473 | 2.8335 | 58.33 |
| 관악구 | 21,391 | 712 | 3.3285 | 100.00 |
| 서초구 | 40,998 | 937 | 2.2855 | 4.17 |
| 강남구 | 62,644 | 1,559 | 2.4887 | 20.83 |
| 송파구 | 42,490 | 1,166 | 2.7442 | 50.00 |
| 강동구 | 23,949 | 683 | 2.8519 | 75.00 |

분포: mean 2.7430%, median 2.7442%, population std 0.3187%p, min 2.1674%, max 3.3285%. 상위 5개구는 관악·강북·마포·은평·강서, 하위 5개구는 중구·서초·종로·용산·구로다. 최대값은 평균에서 약 1.84σ로 단일 extreme outlier로 보기는 어렵다.

## Normalization과 최근 분기 후보

- raw rate: 설명력은 높지만 작은 절대 범위 1.1611%p를 score budget에 연결할 정책이 필요하다.
- min-max: 최솟값/최댓값 두 구가 0/100을 고정해 분기별 extreme 변화에 민감하다.
- average-rank percentile: 서울 내 상대위험과 기존 OA/commercial 관례에 맞고 outlier 크기에 덜 민감하다. 추천 normalization이다.
- z-score: 진단에는 유용하지만 음수와 무제한 범위를 score로 다시 매핑해야 한다.

비교할 구조 metric은 A 최신분기 percentile, B 최근 4분기 단순평균 후 percentile, C 최근 4분기 `Σclosed/Σtotal` pooled rate 후 percentile이다. 현재는 2026Q1만 있어 A=B=C가 사실상 같으며 안정성을 비교할 수 없다. 최종 추천은 **C, 최근 4개 complete 분기 pooled closure rate의 서울 25구 average-rank percentile**이다. 점포수가 다른 분기의 분모를 보존하고 단일 분기 노이즈를 줄이기 때문이다. 3개 과거 분기 확보 전에는 추천을 production 확정으로 간주하지 않는다.

## 현재 risk budget과 base preview

현재 공식 구조:

- base 20
- content 최대 12
- weather 최대 54: 폭풍 28 + 강수 8 + 바람 8 + 극단기온 10. weather 전체 cap은 없음
- special-day 최대 8
- 산술 최대 94, 최종 clamp 0~100
- risk는 높을수록 위험이며 opportunity에는 inverse risk로 반영

새 bonus보다 base20의 일부를 분기 구조위험으로 바꾸는 편이 의미와 총 budget을 보존한다. 비교용으로 모든 구에 동일한 content 4 + weather 16 + special-day 3 = 23을 고정했다.

| 구조 | 25구 risk mean/median/std/min/max | 해석 |
|---|---|---|
| 기존 base20 | 43 / 43 / 0 / 43 / 43 | 지역 차이 없음 |
| A structural 0~20 | 33 / 33 / 6.009 / 23 / 43 | 저위험 구의 기존 baseline을 전부 제거해 변화가 큼 |
| B fixed10 + structural 0~10 | 38 / 38 / 3.005 / 33 / 43 | 고정 안전판과 지역 구분력의 균형 |
| C fixed15 + structural 0~5 | 40.5 / 40.5 / 1.502 / 38 / 43 | 보수적이나 차별성이 작음 |

주요 구 A/B/C 최종 risk: 강남 27.166/35.083/39.042, 마포 41.334/42.167/42.584, 종로 24.666/33.833/38.417, 송파 33/38/40.5, 금천 29.666/36.333/39.667, 강서 39.666/41.333/42.167, 중구 23/33/38, 서초 23.834/33.417/38.209, 중랑 38.834/40.917/41.959.

세 후보 모두 대표 시나리오에서 0/100 saturation이 없고 단기 contribution도 유지된다. Production 구조 후보로는 **B, fixed base10 + structural percentile 최대 10**을 추천한다. 기존 base를 전부 제거하지 않으면서 측정 baseline이 유의미한 차이를 만든다. 최대 10은 아직 preview budget이며 확정 cap이 아니다.

## Snapshot, fallback, semantics

- 원본은 복제하지 않고 consumption snapshot 경로만 참조한다.
- `aggregated/2026Q1.json`: 합산 수와 정합성 diagnostics
- `normalized/2026Q1.json`: raw/min-max/percentile/z-score와 A/B/C preview
- 요청 분기 complete snapshot 우선, 없으면 마지막 complete snapshot 사용
- metadata: requested/source quarter, age quarters, source status
- hard freshness cutoff 없음
- unavailable/partial: 0%로 취급하지 않고 기존 base20 유지, 나머지 contribution 재정규화 금지

향후 표현은 “서울시 상권분석서비스의 분기별 점포 폐업률을 기반으로 한 자치구 상대 구조적 영업 위험”이다. 개별 가게의 미래 폐업확률이나 실시간 위험이 아니다. KOSIS는 전국·산업별 연간 `기업 소멸률` evidence/benchmark로만 유지하며 OA-22173 `점포 폐업률`과 결합하지 않는다.

## Production 전 blocker

1. 2025Q2~2025Q4 complete OA-22173 snapshot 확보와 rank/분기 변동 검증
2. 종료된 OA-22173의 공식 대체 서비스 또는 지속 가능한 snapshot 갱신 경로 확인
3. 4분기 pooled percentile 정책 승인
4. fixed10 + structural max10 budget 승인
5. 부분 snapshot completeness 기준 확정
