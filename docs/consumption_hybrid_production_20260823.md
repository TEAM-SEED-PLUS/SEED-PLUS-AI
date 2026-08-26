# [F] 소비 하이브리드 production 전환

- 전환일: 2026-08-23 KST
- 최종 flag: `CONSUMPTION_HYBRID_ENABLED=True`
- baseline source: 요청 `2026Q3` → complete aligned fallback `2026Q1`, `age_quarters=2`
- realtime source: OA-22385 `CMRCL_TIME=20260823 1600`, 82/82 정상

## Production 공식

```text
sales_baseline_bonus
  = clamp(0, 7, sales_per_matched_store_percentile / 100 × 7)

realtime_commerce_bonus
  = clamp(0, 3, mean(place_payment_midpoint_percentiles) / 100 × 3)

normal consumption_hybrid_bonus
  = sales_baseline_bonus + realtime_commerce_bonus  # max 10
```

기존 OA-21285 spending 최대 +10 예산을 위 `+7/+3`으로 교체했다. 정상 hybrid에서는 OA-21285 spending을 추가하지 않는다. OA-21285 inflow, TourAPI spending 최대 +10, content, weather, special-day, base 24, final clamp, competition, risk는 유지한다.

## Mode와 fallback

| 조건 | mode | 적용 |
|---|---|---|
| baseline 정상 + realtime 정상 | `sales_baseline_plus_realtime` | +7 baseline + +3 realtime |
| baseline 정상 + realtime 실패/무자료/날짜·band 불일치 | `sales_baseline_only` | baseline만, +10 재정규화 없음 |
| baseline unavailable | `legacy_oa_spending_fallback` | 기존 OA-21285 spending max10; realtime 추가 없음 |

심야에는 OA-22385 current를 사용하지 않는다. 다른 날짜나 다른 time band도 `no_data` 처리한다. 중랑구는 장소가 없으므로 `sales_baseline_only`다.

## 실제 25개구 production preview

| 자치구 | 동일업종 점포당 매출 | baseline Pctl | +7 | realtime Pctl | +3 | hybrid | mode |
|---|---:|---:|---:|---:|---:|---:|---|
| 강남구 | 58,563,166.54 | 83.33 | 5.8331 | 49.6150 | 1.4885 | 7.3216 | baseline+realtime |
| 강동구 | 37,592,833.65 | 50.00 | 3.5000 | 37.3450 | 1.1203 | 4.6203 | baseline+realtime |
| 강북구 | 41,881,198.00 | 62.50 | 4.3750 | 31.1750 | 0.9353 | 5.3103 | baseline+realtime |
| 강서구 | 34,983,886.96 | 16.67 | 1.1669 | 43.2100 | 1.2963 | 2.4632 | baseline+realtime |
| 관악구 | 33,992,948.87 | 12.50 | 0.8750 | 54.0150 | 1.6204 | 2.4954 | baseline+realtime |
| 광진구 | 38,507,951.58 | 54.17 | 3.7919 | 38.5800 | 1.1574 | 4.9493 | baseline+realtime |
| 구로구 | 35,803,130.92 | 29.17 | 2.0419 | 24.4833 | 0.7345 | 2.7764 | baseline+realtime |
| 금천구 | 84,130,645.45 | 91.67 | 6.4169 | 74.6900 | 2.2407 | 8.6576 | baseline+realtime |
| 노원구 | 37,583,346.21 | 45.83 | 3.2081 | 91.3600 | 2.7408 | 5.9489 | baseline+realtime |
| 도봉구 | 36,952,388.41 | 41.67 | 2.9169 | 35.8000 | 1.0740 | 3.9909 | baseline+realtime |
| 동대문구 | 90,478,312.87 | 95.83 | 6.7081 | 22.8400 | 0.6852 | 7.3933 | baseline+realtime |
| 동작구 | 110,478,624.67 | 100.00 | 7.0000 | 45.0600 | 1.3518 | 8.3518 | baseline+realtime |
| 마포구 | 35,630,847.17 | 20.83 | 1.4581 | 56.1700 | 1.6851 | 3.1432 | baseline+realtime |
| 서대문구 | 35,945,564.47 | 33.33 | 2.3331 | 21.6033 | 0.6481 | 2.9812 | baseline+realtime |
| 서초구 | 51,707,141.03 | 66.67 | 4.6669 | 37.8600 | 1.1358 | 5.8027 | baseline+realtime |
| 성동구 | 33,657,770.20 | 8.33 | 0.5831 | 64.8133 | 1.9444 | 2.5275 | baseline+realtime |
| 성북구 | 29,009,733.82 | 0.00 | 0.0000 | 54.3200 | 1.6296 | 1.6296 | baseline+realtime |
| 송파구 | 54,413,059.29 | 79.17 | 5.5419 | 72.9271 | 2.1878 | 7.7297 | baseline+realtime |
| 양천구 | 35,796,820.32 | 25.00 | 1.7500 | 37.0350 | 1.1111 | 2.8611 | baseline+realtime |
| 영등포구 | 53,982,405.48 | 75.00 | 5.2500 | 68.5200 | 2.0556 | 7.3056 | baseline+realtime |
| 용산구 | 68,649,805.61 | 87.50 | 6.1250 | 52.7783 | 1.5834 | 7.7084 | baseline+realtime |
| 은평구 | 31,029,909.27 | 4.17 | 0.2919 | 32.7200 | 0.9816 | 1.2735 | baseline+realtime |
| 종로구 | 53,582,030.86 | 70.83 | 4.9581 | 61.7890 | 1.8537 | 6.8118 | baseline+realtime |
| 중구 | 40,414,651.00 | 58.33 | 4.0831 | 43.8286 | 1.3149 | 5.3980 | baseline+realtime |
| 중랑구 | 36,923,454.46 | 37.50 | 2.6250 | null | 0.0000 | 2.6250 | baseline-only |

## 주요 9개구 기존 OA proxy 대비 신규

기존 값은 2026-08-22 21:25 OA-21285 누적 snapshot의 자치구 percentile을 max10으로 환산한 비교값이다. 시간 기준이 달라 점수 변화 자체보다 지역 구분 구조 비교용이다.

| 자치구 | 기존 OA spending | 신규 hybrid |
|---|---:|---:|
| 강남구 | 7.826 | 7.3216 |
| 마포구 | 9.565 | 3.1432 |
| 종로구 | 3.913 | 6.8118 |
| 송파구 | 9.130 | 7.7297 |
| 금천구 | 3.043 | 8.6576 |
| 강서구 | 0.435 | 2.4632 |
| 중구 | 5.652 | 5.3980 |
| 서초구 | 3.478 | 5.8027 |
| 중랑구 | 비교 snapshot OA 없음 | 2.6250 baseline-only |

## Distribution 검증

| mean | median | population std | min | max | +9 이상 | +1 이하 |
|---:|---:|---:|---:|---:|---:|---:|
| 4.8831 | 4.9493 | 2.2671 | 1.2735 | 8.6576 | 0 | 0 |

상단 9~10 몰림, 0 근처 몰림, 한두 구만 10에 붙는 현상이 없다. realtime은 raw 금액이 아니라 82개 장소 average-rank percentile의 자치구 평균이므로 outlier와 장소 수 합산 편향을 제어한다.

## Collector와 monitoring

Collector는 다음처럼 실행한다.

```bash
python3 consumption_hybrid.py
```

첫 실행에서 `CMRCL_TIME=20260823 1600`, 82/82 정상, failed 0을 저장했다. 즉시 재실행은 API 1건 probe 후 `unchanged`, `batch_skipped=true`로 종료됐다. 내부 scheduler는 없다.

Monitoring 예시:

```bash
python3 consumption_hybrid.py --monitor-date 2026-08-23 --time-band 점심
```

source quarter/age, 최신 commerce time/age, 자치구별 baseline·realtime percentile, hybrid mode, legacy fallback, failed place 수를 출력한다.

## Contribution semantics

- OA-22176+OA-22173: 서울시 추정매출과 동일 업종 모집단 점포수를 이용한 자치구 상대 소비 수준
- OA-22385: 카드 결제금액 범위를 이용한 상대적 실시간 소비 활성 수준; 서울 전체 소비의 완전한 대표값 아님
- OA-21285: inflow 유동 기회, baseline unavailable 시 spending fallback proxy
- TourAPI: 기존 소비강도 50%·미식 35%·연령 소비 다양성 15%, max10, 3개월 fallback을 유지한 관광 소비 특성

## 완료 판정

aligned baseline complete, 25개구 percentile, OA-22385 mapping·batch, +7/+3 cap, OA spending 비중복, baseline/realtime fallback, TourAPI·inflow·competition·risk 회귀, 분포 검증을 모두 통과했다. 따라서 **[F] 소비 하이브리드는 production 전환 완료**로 판정한다.
