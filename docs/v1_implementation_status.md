# 상권날씨 v1 구현 상태

## 완료

- [A] opportunity weights
- [B] weather thresholds
- [C] decision tags
- [D] time bands/deep-night handling
- [E] missing-data fallback
- [F] OA-21285 measured footfall
- [F] commercial-store competition
- [F] consumption hybrid
- [G] TourAPI tourism baseline

## 검증 후 production 제외

`[F] KOSIS / closure structural operational risk`는 미구현이 아니라 **investigated / validated / deferred from production** 상태다.

- `STRUCTURAL_CLOSURE_RISK_ENABLED = False`
- `structural_risk_status = deferred`
- Production 공식은 `base20 + content + weather + special-day` 그대로다.
- KOSIS는 `annual_industry_benchmark`: 연간 기업 소멸률 참고자료이며 district score에 미사용한다.
- OA-22172/OA-22173은 `historical_only`: 과거 서울시 점포 폐업률 diagnostics이며 production에 미사용한다.
- 제외 reason: `no_current_official_district_refresh_source`, `kosis_no_seoul_district_grain`, `legacy_seoul_dataset_discontinued`

검증된 historical candidate는 `2025Q2~2026Q1 최근 4 complete 분기의 pooled closure rate → 서울 25구 average-rank percentile`이고 candidate base는 `fixed10 + structural max10`이다. `production_applied=false`다.

Historical validation 요약:

- 25 districts complete
- pooled closure rate 2.0344%~3.1118%
- Spearman: Q2–Q3 0.7354, Q3–Q4 0.8338, Q4–Q1 0.7277, Q1–pooled 0.8831
- Production activation: official refresh source lifecycle 때문에 blocked

## 향후 activation gate

다음 조건을 모두 다시 검증하고 명시적으로 승인하기 전에는 flag를 자동 활성화하지 않는다.

1. 공식적으로 유지되는 source
2. 서울 25개구 또는 검증 가능한 하위 spatial grain
3. 폐업점포수+전체점포수 또는 공식 폐업률
4. 최소 quarterly/yearly refresh 확인
5. Historical metric과 의미 consistency 확인
6. 서울 25개구 complete coverage
7. 전체 regression 통과

현재 production LLM에는 폐업률 또는 KOSIS 기반 자치구 risk가 적용됐다고 전달하지 않는다. Historical diagnostics에서만 “과거 서울시 점포 폐업률 기반 구조위험 참고값”이라고 표현한다.
