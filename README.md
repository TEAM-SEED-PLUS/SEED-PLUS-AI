# 상권날씨 (Weather Feed) v1

상권날씨는 서울 25개 자치구의 외부 데이터를 수집·정규화하여  
유입 압력, 소비 의도, 경쟁 압박, 운영 리스크**의 4개 지표를 계산하고,  
이를 기반으로 상권날씨와 의사결정 태그를 생성하는 기능입니다.
점수, 상권날씨, 의사결정 태그는 룰 기반으로 계산됩니다.
OpenAI API Key가 없는 환경에서도 정상 실행되며,  
판단문장, 근거문장, 추천 액션은 `rule_fallback` 방식으로 생성됩니다.
추후 OpenAI API Key가 등록되면 동일한 Feed Schema를 유지한 상태에서  
판단문장, 근거문장, 추천 액션 생성에 LLM을 사용할 수 있습니다.

---

## 1. Repository Clone

```bash
git clone https://github.com/TEAM-SEED-PLUS/SEED-PLUS-AI.git
```

---

## 2. ㅠㅠㅠPython 가상환경 생성

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### Windows

```cmd
python -m venv .venv
.venv\Scripts\activate
```

---

## 3. Dependency 설치

### macOS / Linux

```bash
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt
```

### Windows

```cmd
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Playwright 기반 수집 기능을 실제로 사용하는 경우 Chromium을 추가 설치합니다.

### macOS / Linux

```bash
python3 -m playwright install chromium
```

### Windows

```cmd
python -m playwright install chromium
```

---

## 4. 환경변수

`.env.example`을 복사하여 `.env` 파일을 생성합니다.

### macOS / Linux

```bash
cp .env.example .env
```

### Windows

```cmd
copy .env.example .env
```

현재 사용하는 환경변수는 다음과 같습니다.

```env
DATA_GO_KR_SERVICE_KEY=
SEOUL_OPEN_DATA_API_KEY=
KOPIS_API_KEY=
TOURISM_DATA_API_KEY=
COMMERCIAL_STORE_API_KEY=
KOSIS_API_KEY=
OPENAI_API_KEY=
OPENAI_MODEL=
```

실제 API Key는 Git repository에 commit하지 않습니다.

---

## 5. 상권날씨 Feed 실행

### macOS / Linux

```bash
python3 demo_public_feed.py \
  --district 강남구 \
  --date 2026-08-22 \
  --time 21:25
```

### Windows

```cmd
python demo_public_feed.py --district 강남구 --date 2026-08-22 --time 21:25
```

---

## 6. 입력값

| 입력값 | 타입 | 필수 여부 | 형식 | 기본값 | 예시 |
|---|---|---|---|---|---|
| district | string | 필수 | 서울 자치구명 | 없음 | 강남구 |
| date | string | 선택 | YYYY-MM-DD | 일반 pipeline에서는 Asia/Seoul 기준 현재 날짜 | 2026-08-22 |
| time | string | 선택 | HH:MM | 일반 pipeline에서는 Asia/Seoul 기준 현재 시각(분 단위) | 21:25 |

`date`, `time`은 요청 입력에서는 선택 사항입니다.

일반 Feed pipeline에서 `date`, `time`을 생략하면  
Asia/Seoul 기준 현재 날짜와 현재 시각을 사용합니다.

단, 검증 및 캡처용 `demo_public_feed.py`에서는 재현 가능한 QA 결과를 제공하기 위해  
생략 시 고정된 검증 날짜·시각을 기본값으로 사용합니다.

응답의 `query.date`, `query.time`에는 입력 생략 여부와 관계없이  
최종적으로 정규화된 날짜와 시간이 항상 포함됩니다.

시간대는 다음 기준으로 구분합니다.

- 심야: 00:00 ~ 06:00
- 아침: 06:00 ~ 12:00
- 점심: 12:00 ~ 17:00
- 오후: 17:00 ~ 20:00
- 저녁: 20:00 ~ 24:00

---

## 7. 실행 결과 예시

```text
============================================================
상권날씨 v1 · 실제 Feed 결과
============================================================

지역: 강남구
기준: 2026-08-22 21:25
시간대: 저녁
Data mode: validated_snapshot

오늘의 상권날씨: ☁️ 흐림
기회점수: 52

[핵심 지표]
유입 압력      48
소비 의도      30
경쟁 압박      29
운영 리스크    20

[의사결정 태그]
- 관망 권장

[판단]
뚜렷한 상승 신호가 강하지 않아,
추가 집행보다는 관찰과 보수적 운영이 적합한 구간입니다.

[근거]
OA-21285 유동값은 동일 시점 서울 자치구 내 상대 위치를 이용한 지표이며,
raw population을 정확한 방문자 수로 단정하지 않습니다.

[추천 액션]
1. 유입 압력 48점 기준으로 대형 광고보다 단골·예약 고객 대상 저비용 알림을 우선하세요.
2. 소비 의도 30점 기준으로 고가 세트보다 진입 장벽 낮은 소액 메뉴를 먼저 제안하세요.
3. 운영 리스크 20점이 낮은 편이므로 재고는 평시보다 소폭만 늘리고 회전율을 확인하세요.

[데이터 상태]
상태: partial

실제 Fallback:
- consumption_baseline

데이터 없음:
- weather
- content.festival
- content.event
- content.performance
- content.sports
- special_day
- competition_sdot

오래되어 미적용:
- tourism

시점 불일치로 미적용:
- realtime_commerce

정상 조회 / 해당 항목 없음:
- 없음

실패:
- 없음

데이터 부족: True

[문장 생성 방식]
rule_fallback
(OpenAI API 키 없이 룰 기반으로 생성)

Data source: 최종 통합 QA에서 검증한 저장 snapshot replay
============================================================
```

위 결과는 실제 프론트엔드 UI가 아니라  
Feed Schema v1 결과를 사람이 확인하기 쉽게 터미널에 출력한 예시입니다.
`Data mode: validated_snapshot`은 실시간 외부 API를 새로 호출한 결과가 아니라,  
최종 통합 QA에서 검증하고 저장한 snapshot을 다시 사용하여 동일한 Feed 결과를 재현하는 방식입니다.
따라서 위 demo 결과는 특정 검증 시점의 저장 데이터를 기준으로 한 재현 결과이며,  
현재 시점의 실시간 상권 상태를 의미하지 않습니다.
실제 Feed pipeline에서는 입력된 날짜·시간 또는  
생략 시 Asia/Seoul 기준 현재 날짜·시간을 기준으로 처리됩니다.

---

## 8. Feed Schema v1

Backend 및 Frontend에서는  
`schema_version = "1.0"`인 Public Feed 결과를 기준으로 연동합니다.

### Top-level Fields

| 필드 | 타입 | 필수 | 의미 |
|---|---|---|---|
| schema_version | string | 필수 | Feed Schema 버전 |
| query | object | 필수 | 지역·날짜·시간·시간대 |
| opportunity_score | number | 필수 | 최종 기회점수 |
| market_weather | object | 필수 | 상권날씨 정보 |
| indicators | object | 필수 | 4개 핵심 지표 |
| decision_tags | string[] | 필수 | 의사결정 태그 |
| narrative | object | 필수 | 판단·근거·추천 액션 |
| data_quality | object | 필수 | 데이터 품질 및 fallback 상태 |
| sources | object | 필수 | 사용 데이터 source의 public metadata |
| generated_at | string | 필수 | Feed 생성 시각 |

---

### indicators

| 필드 | 타입 | 의미 |
|---|---|---|
| inflow_pressure | number | 유입 압력 |
| spending_intent | number | 소비 의도 |
| competition_pressure | number | 경쟁 압박 |
| operational_risk | number | 운영 리스크 |

---

### narrative

| 필드 | 타입 | 의미 |
|---|---|---|
| generation_mode | string | 문장 생성 방식 |
| judgement_sentence | string | 현재 상권 상태에 대한 판단 |
| basis_sentence | string | 판단 근거 |
| recommended_actions | string[] | 추천 액션 목록 |

`generation_mode`은 다음 값을 사용합니다.

```text
rule_fallback
hybrid_llm
```

- `rule_fallback`: OpenAI API를 사용하지 않고 룰 기반으로 문장을 생성한 경우
- `hybrid_llm`: OpenAI 호출 및 결과 검증이 정상 완료되어 LLM 문장을 사용한 경우

LLM 사용 여부와 관계없이  
점수, 상권날씨, 의사결정 태그는 변경되지 않습니다.
        
---

### data_quality

`data_quality`는 Feed 계산에 사용된 외부 데이터의 품질과  
fallback 여부, 점수 기준 시각에 대한 정보를 제공합니다.

| 필드 | 타입 | 의미 |
|---|---|---|
| status | string | 전체 데이터 품질 상태 (`ok`, `partial`, `fallback`, `no_data`) |
| data_insufficient | boolean | 점수 계산에 필요한 데이터가 일부 부족한지 여부 |
| badges | string[] | 심야 또는 데이터 부족 관련 안내 문구 |
| fallback_sources | string[] | 대체값 또는 이전 snapshot을 실제 점수 계산에 사용한 source |
| no_data_sources | string[] | 필요한 데이터를 확보하지 못한 source |
| stale_sources | string[] | freshness 기준을 초과하여 점수에 사용하지 않은 source |
| skipped_sources | string[] | 날짜·시간대·eligibility 조건 불일치로 점수에 반영하지 않은 source |
| failed_sources | string[] | API 호출, 파싱 또는 처리 과정에서 실패한 source |
| empty_sources | string[] | 정상 조회됐지만 해당 조건에서 결과가 0건인 source |
| score_context | object | 점수 계산 기준 날짜·시간대 관련 metadata |

`score_context`에는 값이 존재하는 항목만 포함되며 다음 필드를 가질 수 있습니다.

| 필드 | 의미 |
|---|---|
| basis | 점수 기준 방식 |
| reference_date | 점수 계산 기준 날짜 |
| reference_time_band | 참조 시간대 |
| reference_start | 참조 시작 시각 |
| reference_end | 참조 종료 시각 |
| representative_time | 대표 시각 |

---

### sources

`sources`는 점수 계산에 사용된 raw data 자체가 아니라,  
각 데이터 source/provider의 **상태, 기준 기간, freshness, eligibility를 요약한 public metadata**입니다.

API Key, secret, raw API response, exception detail 및 내부 contribution diagnostics는 포함되지 않습니다.

`sources`는 Public Feed 응답에 항상 포함되지만,  
프론트엔드에서 점수·날씨·태그를 표시하기 위해 반드시 소비해야 하는 핵심 필드라기보다는  
데이터 출처와 상태를 확인하기 위한 참고용 metadata입니다.

`sources`에는 다음 source key가 항상 생성됩니다.

```text
weather
content.festival
content.event
content.performance
content.sports
special_day
footfall
tourism
commercial_store
consumption_baseline
realtime_commerce
competition_sdot
```

모든 개별 source 객체에는 다음 공통 필드가 존재합니다.

| 필드 | 타입 | 필수 | 의미 |
|---|---|---|---|
| source | string | 필수 | 실제 public source/provider 이름 |
| status | string | 필수 | 해당 source의 상태 |

source의 `status`는 다음 값을 사용할 수 있습니다.

```text
ok
empty
partial
fallback
no_data
failed
complete
```

그 외 metadata는 source 종류에 따라 선택적으로 추가됩니다.

| Source | 선택 Metadata |
|---|---|
| content.festival | `item_count` |
| content.event | `item_count` |
| content.performance | `item_count` |
| content.sports | `item_count` |
| special_day | `item_count` |
| footfall | `source_time`, `snapshot_count`, `fallback` |
| tourism | `requested_month`, `source_month`, `age_months`, `fallback` |
| commercial_store | `mode`, `requested_quarter`, `source_quarter`, `age_quarters`, `fallback` |
| consumption_baseline | `requested_quarter`, `source_quarter`, `age_quarters` |
| realtime_commerce | `source_time`, `age_minutes`, `valid_place_count`, `eligibility_reason` |

선택 metadata 값이 `None`인 경우 해당 필드는 응답에서 생략될 수 있습니다.

---

## 9. JSON 응답 예시

아래 JSON은 강남구 `validated_snapshot` demo에서 생성된 Public Feed 구조를 기준으로 한 예시입니다.

`sources`는 실제 serializer가 반환하는 public metadata 구조를 사용합니다.

```json
{
  "schema_version": "1.0",
  "query": {
    "district": "강남구",
    "date": "2026-08-22",
    "time": "21:25",
    "time_band": "저녁"
  },
  "opportunity_score": 52,
  "market_weather": {
    "grade": "흐림",
    "emoji": "☁️"
  },
  "indicators": {
    "inflow_pressure": 48,
    "spending_intent": 30,
    "competition_pressure": 29,
    "operational_risk": 20
  },
  "decision_tags": [
    "관망 권장"
  ],
  "narrative": {
    "generation_mode": "rule_fallback",
    "judgement_sentence": "뚜렷한 상승 신호가 강하지 않아, 추가 집행보다는 관찰과 보수적 운영이 적합한 구간입니다.",
    "basis_sentence": "OA-21285 유동값은 동일 시점 서울 자치구 내 상대 위치를 이용한 지표입니다.",
    "recommended_actions": [
      "유입 압력을 고려해 대형 광고보다 단골·예약 고객 대상 저비용 알림을 우선하세요.",
      "소비 의도를 고려해 고가 세트보다 진입 장벽이 낮은 메뉴를 먼저 제안하세요.",
      "운영 리스크를 고려해 재고는 평시보다 소폭만 늘리고 회전율을 확인하세요."
    ]
  },
  "data_quality": {
    "status": "partial",
    "data_insufficient": true,
    "badges": [],
    "fallback_sources": [
      "consumption_baseline"
    ],
    "no_data_sources": [
      "weather",
      "content.festival",
      "content.event",
      "content.performance",
      "content.sports",
      "special_day",
      "competition_sdot"
    ],
    "stale_sources": [
      "tourism"
    ],
    "skipped_sources": [
      "realtime_commerce"
    ],
    "failed_sources": [],
    "empty_sources": [],
    "score_context": {}
  },
  "sources": {
    "weather": {
      "source": "weather",
      "status": "no_data"
    },
    "content": {
      "festival": {
        "source": "festival",
        "status": "no_data",
        "item_count": 0
      },
      "event": {
        "source": "event",
        "status": "no_data",
        "item_count": 0
      },
      "performance": {
        "source": "performance",
        "status": "no_data",
        "item_count": 0
      },
      "sports": {
        "source": "sports",
        "status": "no_data",
        "item_count": 0
      }
    },
    "special_day": {
      "source": "special_day",
      "status": "no_data",
      "item_count": 0
    },
    "footfall": {
      "source": "oa21285",
      "status": "ok",
      "source_time": "2026-08-22 21:25",
      "snapshot_count": 5,
      "fallback": false
    },
    "tourism": {
      "source": "tourapi",
      "status": "no_data",
      "requested_month": "202608",
      "source_month": "202509",
      "age_months": 11
    },
    "commercial_store": {
      "source": "commercial_store",
      "status": "complete",
      "mode": "commercial_only",
      "requested_quarter": "2026Q3",
      "source_quarter": "2026Q3",
      "age_quarters": 0
    },
    "consumption_baseline": {
      "source": "OA-22176+OA-22173",
      "status": "fallback",
      "requested_quarter": "2026Q3",
      "source_quarter": "2026Q1",
      "age_quarters": 2
    },
    "realtime_commerce": {
      "source": "oa22385",
      "status": "no_data",
      "source_time": "20260823 1600",
      "age_minutes": 6009.39,
      "valid_place_count": 0,
      "eligibility_reason": "different_date_or_time_band"
    },
    "competition_sdot": {
      "source": "sdot",
      "status": "no_data"
    }
  },
  "generated_at": "..."
}
```

> `realtime_commerce.age_minutes`는 serializer 실행 시점을 기준으로 계산되는 값이므로  
> 동일한 demo를 다른 시각에 실행하면 값이 달라질 수 있습니다.

상세 Schema는 다음 문서를 참고합니다.

```text
docs/weather_feed_schema_v1.md
```

---

### OpenAI API Key가 없는 경우

```text
generation_mode = rule_fallback
```

점수, 상권날씨, 의사결정 태그와 함께  
룰 기반 판단문장, 근거문장, 추천 액션이 생성됩니다.

### OpenAI API Key를 사용하는 경우

`.env`에 다음 환경변수를 설정합니다.

```env
OPENAI_API_KEY=발급받은_API_KEY
OPENAI_MODEL=사용할_모델
```

LLM 호출 및 결과 검증이 정상적으로 완료되면:

```text
generation_mode = hybrid_llm
```

으로 표시됩니다.
LLM은 점수, 상권날씨, 의사결정 태그를 변경하지 않습니다.

---

## 11. Test

### macOS / Linux

```bash
python3 -m unittest discover -s tests
```

### Windows

```cmd
python -m unittest discover -s tests
```

현재 검증된 실행 결과:

```text
Ran 213 tests in ...
OK
```

테스트가 추가될 경우 전체 테스트 개수는 증가할 수 있습니다.

---

## 12. Windows UTF-8

프로젝트 내부 텍스트 파일 입출력에는 UTF-8 encoding을 명시하여  
Windows 기본 `cp949` 인코딩에 암묵적으로 의존하지 않도록 구성했습니다.

현재 코드 기준으로 `Path.open()`, `read_text()`, `write_text()` 등  
프로젝트 내부 텍스트 파일 입출력의 encoding 누락 여부를 점검했으며,  
관련 수정 후 전체 213개 테스트가 정상 통과했습니다.

이전 버전 코드에서 `cp949` 관련 오류가 발생하는 경우에는  
임시로 다음 설정을 사용할 수 있습니다.

### CMD

```cmd
set PYTHONUTF8=1
```

### PowerShell

```powershell
$env:PYTHONUTF8="1"
```

최신 코드에서는 별도의 `PYTHONUTF8=1` 설정 없이 실행할 수 있도록  
파일 입출력 encoding을 명시하고 있습니다.
