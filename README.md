# 상권날씨 (Weather Feed) v1

상권날씨는 서울 25개 자치구의 외부 데이터를 수집·정규화하여 다음 4개 지표를 계산하고, 이를 기반으로 상권날씨와 의사결정 태그를 생성하는 기능입니다.

- 유입 압력
- 소비 의도
- 경쟁 압박
- 운영 리스크

점수, 상권날씨, 의사결정 태그는 룰 기반으로 계산됩니다.

OpenAI API Key가 없는 환경에서도 정상 실행되며, 판단문장·근거문장·추천 액션은 `rule_fallback` 방식으로 생성됩니다.

추후 OpenAI API Key가 등록되면 동일한 Feed Schema v1을 유지하면서 문장 생성에 LLM을 사용할 수 있습니다.

---

## 1. Repository Clone

```bash
git clone https://github.com/TEAM-SEED-PLUS/SEED-PLUS-AI.git
cd SEED-PLUS-AI
```

---

## 2. Python 가상환경 생성

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

Playwright는 Sports collector에서 사용합니다.

로컬 환경에서 Sports collector까지 실행하려면 Chromium을 추가 설치합니다.

### macOS / Linux

```bash
python3 -m playwright install chromium
```

### Windows

```cmd
python -m playwright install chromium
```

Linux production collector image에서는 Chromium OS dependency도 필요하므로 다음 방식 또는 이에 준하는 Dockerfile 구성을 사용합니다.

```bash
python -m playwright install --with-deps chromium
```

FastAPI request path에서는 Playwright/Chromium을 실행하지 않습니다. FastAPI는 생성된 Sports snapshot만 읽습니다.

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

사용하는 환경변수는 다음과 같습니다.

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

### 환경변수 용도

| 환경변수 | 주요 용도 | 필수 여부 |
|---|---|---|
| `DATA_GO_KR_SERVICE_KEY` | 기상청 날씨, 축제, 특일 데이터 | production 데이터 품질을 위해 권장 |
| `SEOUL_OPEN_DATA_API_KEY` | 서울 문화행사, OA-21285, OA-22385, S-DoT 계열 | production collector 사용 시 필요 |
| `KOPIS_API_KEY` | 공연정보 snapshot collector | 공연 콘텐츠 사용 시 필요 |
| `TOURISM_DATA_API_KEY` | 관광 데이터 snapshot 갱신 | snapshot 갱신 시 필요 |
| `COMMERCIAL_STORE_API_KEY` | 상가 관련 별도 수집/연구 경로 | 현재 local scoring snapshot 기반 request path의 필수 blocker는 아님 |
| `KOSIS_API_KEY` | KOSIS refresh/probe | 관련 offline 작업 시 필요 |
| `OPENAI_API_KEY` | LLM narrative 생성 | 선택 |
| `OPENAI_MODEL` | OpenAI 사용 모델 | `OPENAI_API_KEY` 사용 시 설정 |

`OPENAI_API_KEY`가 없으면 API 호출 없이 즉시 `rule_fallback` 문장을 생성합니다.

---

# 5. FastAPI 실행

FastAPI는 Public Feed Schema v1 JSON을 wrapper 없이 반환하는 내부 AI/Data 서비스입니다.

`.env`는 production pipeline module import 전에 로드합니다.

### macOS / Linux

```bash
python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### Windows

```cmd
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

로컬 Swagger UI:

```text
http://localhost:8000/docs
```

OpenAPI JSON:

```text
http://localhost:8000/openapi.json
```

Health check:

```text
GET /health
```

동일 Docker Compose network의 Spring container에서는 다음 주소 사용을 권장합니다.

```text
http://ai:8000
```

FastAPI `8000` port는 외부에 직접 공개하지 않고 Spring Backend에서만 접근하는 internal service 구성을 권장합니다.

FastAPI 자체에는 현재 별도 JWT, `x-api-key`, CORS 설정이 없습니다.

---

# 6. API

## 6.1 Health

```http
GET /health
```

성공:

```json
{
  "status": "ok"
}
```

---

## 6.2 자치구 Detail Feed

```http
GET /api/v1/weather-feeds
```

### Query Parameters

| 파라미터 | 필수 | 형식 | 예시 |
|---|---:|---|---|
| `district` | O | 서울 자치구명 | `강남구` |
| `date` | X | `YYYY-MM-DD` | `2026-09-12` |
| `time` | X | `HH:MM` | `14:00` |
| `time_band` | X | `심야`, `아침`, `점심`, `오후`, `저녁` | `점심` |

예:

```http
GET /api/v1/weather-feeds?district=강남구&date=2026-09-12&time_band=점심
```

성공 시 Public Feed Schema v1 raw JSON을 반환합니다.

HTTP status:

| Status | 의미 |
|---|---|
| `200` | 정상 Feed 반환 |
| `422` | district/date/time/time_band 입력 오류 또는 time-time_band 불일치 |
| `500` | 내부 pipeline 실행 실패 |

외부 API 원문, API Key, exception detail은 오류 응답에 노출하지 않습니다.

---

## 6.3 서울 25개 자치구 Overview

```http
GET /api/v1/weather-feeds/overview
```

서울 지도 전체를 한 번에 렌더링하기 위한 snapshot 기반 API입니다.

### Query Parameters

| 파라미터 | 필수 | 형식 | 예시 |
|---|---:|---|---|
| `date` | X | `YYYY-MM-DD` | `2026-09-12` |
| `time` | X | `HH:MM` | `10:38` |
| `time_band` | X | `심야`, `아침`, `점심`, `오후`, `저녁` | `아침` |

예:

```http
GET /api/v1/weather-feeds/overview?date=2026-09-12&time_band=아침
```

응답 예:

```json
{
  "schema_version": "1.0",
  "query": {
    "date": "2026-09-12",
    "time": "09:00",
    "time_band": "아침"
  },
  "status": "ok",
  "districts": [
    {
      "district": "강남구",
      "opportunity_score": 67,
      "grade": "구름",
      "emoji": "⛅"
    }
  ],
  "generated_at": "2026-09-12T15:16:41+09:00",
  "source_time": "2026-09-12T15:13:34+09:00",
  "age_minutes": 3.13,
  "fresh_ttl_minutes": 10,
  "is_fresh": true
}
```

정상 snapshot은 `districts`에 서울 25개 자치구를 포함합니다.

Overview HTTP 요청에서는 25개 Detail Feed를 새로 계산하지 않습니다.

HTTP request path에서 collector를 실행하거나 외부 source를 호출해 Overview를 재생성하지 않습니다.

---

# 7. Time Band 계약

공식 시간대는 다음과 같습니다.

| time_band | 범위 | representative_time |
|---|---|---:|
| 심야 | `00:00 <= time < 06:00` | `03:00` |
| 아침 | `06:00 <= time < 12:00` | `09:00` |
| 점심 | `12:00 <= time < 17:00` | `14:00` |
| 오후 | `17:00 <= time < 20:00` | `18:00` |
| 저녁 | `20:00 <= time < 24:00` | `21:00` |

### 입력 규칙

`time`만 입력:

```text
입력한 실제 time 사용
```

`time_band`만 입력:

```text
해당 band의 representative_time 사용
```

둘 다 입력하고 일치:

```text
허용
```

예:

```text
time=10:38
time_band=아침
```

둘 다 입력하지만 서로 불일치:

```text
HTTP 422
```

예:

```text
time=10:38
time_band=저녁
```

둘 다 생략:

```text
Asia/Seoul 현재 날짜·시각 사용
```

### Detail과 Overview의 query.time 차이

Detail Feed:

```text
실제 normalized 요청 time을 유지
```

예:

```text
10:38 + 아침
→ query.time = 10:38
```

Overview:

```text
snapshot representative_time을 반환
```

예:

```text
10:38 + 아침
→ query.time = 09:00
```

---

# 8. Public Feed Schema v1

Backend 및 Frontend에서는:

```text
schema_version = "1.0"
```

을 기준으로 연동합니다.

## Top-level Fields

| 필드 | 타입 | 의미 |
|---|---|---|
| `schema_version` | string | Feed Schema 버전 |
| `query` | object | district/date/time/time_band |
| `opportunity_score` | number | 최종 기회점수 |
| `market_weather` | object | 상권날씨 score/grade/emoji |
| `indicators` | object | 4개 핵심 지표 |
| `decision_tags` | string[] | 의사결정 태그 |
| `narrative` | object | 판단·근거·추천 액션 |
| `data_quality` | object | 데이터 품질 상태 |
| `sources` | object | source 상태 metadata |
| `content` | object | Frontend 표시용 콘텐츠 |
| `generated_at` | string | Feed 생성 시각 |

---

## indicators

```json
{
  "inflow_pressure": 63,
  "spending_intent": 60,
  "competition_pressure": 46,
  "operational_risk": 21
}
```

---

## market_weather

```json
{
  "score": 64,
  "grade": "흐림",
  "emoji": "☁️"
}
```

---

## narrative

```json
{
  "generation_mode": "rule_fallback",
  "judgement_sentence": "...",
  "basis_sentence": "...",
  "recommended_actions": [
    "...",
    "...",
    "..."
  ]
}
```

`generation_mode`:

```text
rule_fallback
hybrid_llm
```

- `rule_fallback`: OpenAI API를 사용하지 않은 룰 기반 문장
- `hybrid_llm`: OpenAI 호출 및 결과 검증이 정상 완료된 문장

LLM은 점수, 상권날씨, 지표, decision tag를 변경하지 않습니다.

---

# 9. content.items

Detail Feed는 Frontend에서 표시할 수 있도록 다음 콘텐츠 구조를 제공합니다.

```json
{
  "content": {
    "items": [
      {
        "id": "3377038",
        "type": "festival",
        "title": "2026 국제선명상대회",
        "period": "2026-04-03 ~ 2026-11-12",
        "place": "서울특별시 강남구 봉은사로 531 (삼성동)",
        "thumbnail_url": "https://..."
      }
    ]
  }
}
```

필드:

| 필드 | 타입 | 의미 |
|---|---|---|
| `id` | string | source ID 또는 deterministic ID |
| `type` | string | 콘텐츠 종류 |
| `title` | string | 제목 |
| `period` | string/null | 기간 또는 일시 |
| `place` | string/null | 장소 |
| `thumbnail_url` | string/null | 이미지 URL |

지원하는 `type`:

```text
festival
event
performance
sports
```

`video` type은 AI/Data Weather Feed API에서 생성하지 않습니다.

### `sources.content`와 `content.items`의 차이

`sources.content`:

```text
데이터 source 상태 및 품질 metadata
```

`content.items`:

```text
Frontend 표시용 whitelist projection
```

raw provider response, 내부 debug 정보, secret은 `content.items`에 포함하지 않습니다.

---

# 10. data_quality

예:

```json
{
  "status": "partial",
  "data_insufficient": true,
  "badges": [
    "일부 데이터가 부족해 대체값 또는 기본값을 사용했습니다"
  ],
  "fallback_sources": [
    "weather",
    "consumption_baseline"
  ],
  "no_data_sources": [],
  "stale_sources": [
    "content.performance",
    "content.sports",
    "tourism"
  ],
  "skipped_sources": [
    "realtime_commerce"
  ],
  "failed_sources": [],
  "empty_sources": [
    "special_day",
    "footfall",
    "competition_sdot"
  ],
  "score_context": {
    "basis": "requested_time"
  }
}
```

### 주요 의미

| 필드 | 의미 |
|---|---|
| `fallback_sources` | 대체값 또는 fallback을 실제 사용 |
| `no_data_sources` | 필요한 데이터를 확보하지 못함 |
| `stale_sources` | freshness 기준 초과 |
| `skipped_sources` | 날짜/시간대/eligibility 불일치 |
| `failed_sources` | source 호출/처리 실패 |
| `empty_sources` | 정상 조회됐지만 해당 조건에서 결과 없음 |

`sources`는 Frontend 필수 표시 데이터라기보다 source 상태 확인용 metadata입니다.

---

# 11. Overview Snapshot

Snapshot 기본 경로:

```text
data/weather_overview/latest/
```

동일 날짜에 다음 5개 snapshot이 독립적으로 존재할 수 있습니다.

```text
YYYY-MM-DD_심야.json
YYYY-MM-DD_아침.json
YYYY-MM-DD_점심.json
YYYY-MM-DD_오후.json
YYYY-MM-DD_저녁.json
```

예:

```text
2026-09-12_심야.json
2026-09-12_아침.json
2026-09-12_점심.json
2026-09-12_오후.json
2026-09-12_저녁.json
```

특정 snapshot 생성:

```bash
python3 weather_overview_collector.py \
  --date 2026-09-12 \
  --time-band 아침
```

현재 Asia/Seoul 기준 active band 생성:

```bash
python3 weather_overview_collector.py --active
```

`--active`는 현재 time_band의 snapshot만 갱신합니다.

다른 time_band snapshot은 삭제하거나 비활성화하지 않습니다.

따라서 Frontend는 현재 시간이 아니더라도 이미 생성된 같은 날짜의 다른 time_band를 계속 조회할 수 있습니다.

---

# 12. Overview Freshness 정책

Fresh TTL:

```text
10분
```

### Fresh

```text
age_minutes <= 10
```

응답:

```json
{
  "status": "ok",
  "is_fresh": true,
  "fresh_ttl_minutes": 10
}
```

### Stale

```text
age_minutes > 10
```

또는 snapshot `generated_at`이 없거나 파싱할 수 없는 경우:

```json
{
  "status": "stale",
  "is_fresh": false,
  "fresh_ttl_minutes": 10
}
```

stale snapshot도 마지막 25개 district 데이터를 유지합니다.

즉 Frontend에서는:

```text
status=stale
```

을 `districts=[]`와 동일하게 취급하면 안 됩니다.

필요하면 UI에 “업데이트 지연” 등의 freshness 상태를 표시할 수 있습니다.

`generated_at`을 파싱할 수 없는 경우:

```text
source_time = 원본 값
age_minutes = null
is_fresh = false
status = stale
```

snapshot 파일 자체가 없거나 읽을 수 없는 경우:

```json
{
  "status": "no_data",
  "districts": []
}
```

---

# 13. Production Collector 운영

Collector는 HTTP request lifecycle과 분리된 CLI/job입니다.

## Weather Overview

```bash
python3 weather_overview_collector.py --active
```

권장 실행 주기:

```text
5분
```

collector 실행 시간은 외부 API 상태와 로컬/서버 환경에 따라 달라질 수 있습니다.

로컬 QA에서는 한 번의 active-band 생성에 약 1~2분 이상이 소요될 수 있었습니다.

HTTP 요청에서 collector를 실행하지 않습니다.

### 일별 5개 time_band snapshot 초기 생성

Frontend에서 동일 날짜의 5개 time_band를 모두 선택할 수 있도록 운영하려면,
해당 날짜의 snapshot을 최초 1회 생성해둘 수 있습니다.

```bash
python3 weather_overview_collector.py --date YYYY-MM-DD --time-band 심야
python3 weather_overview_collector.py --date YYYY-MM-DD --time-band 아침
python3 weather_overview_collector.py --date YYYY-MM-DD --time-band 점심
python3 weather_overview_collector.py --date YYYY-MM-DD --time-band 오후
python3 weather_overview_collector.py --date YYYY-MM-DD --time-band 저녁
```

이후 정기 scheduler에서는:

```bash
python3 weather_overview_collector.py --active
```

를 사용하여 현재 active time_band만 5분 주기로 갱신합니다.

이미 생성된 다른 time_band snapshot은 삭제하지 않습니다.

> 5개 snapshot을 매 5분마다 모두 재생성하지 않습니다.
> 정기 refresh 대상은 현재 active time_band 하나입니다.

### Linux cron 예시

중복 실행을 막기 위해 `flock -n` 사용을 권장합니다.

```cron
*/5 * * * * cd /srv/weatherfeed && /usr/bin/flock -n /tmp/weather-overview-collector.lock .venv/bin/python weather_overview_collector.py --active
```

이전 실행이 아직 진행 중이면 다음 실행은 skip됩니다.

### Kubernetes

CronJob 사용 시:

```yaml
concurrencyPolicy: Forbid
```

를 권장합니다.

여러 host가 동일 snapshot volume에 쓰는 경우에는 host-local `/tmp` lock이 아닌 공유/distributed lock 또는 단일 CronJob 구성이 필요합니다.

---

## Sports

```bash
python3 sports_collector.py --date YYYY-MM-DD
```

권장 주기:

```text
10~15분
```

---

## KOPIS Performance

```bash
python3 performance_collector.py --date YYYY-MM-DD
```

필요:

```text
KOPIS_API_KEY
```

권장 주기:

```text
30분
```

---

## OA-21285

```bash
python3 oa21285_collector.py
```

필요:

```text
SEOUL_OPEN_DATA_API_KEY
```

권장 주기:

```text
약 5분
```

---

## OA-22385 실시간 소비

```bash
python3 consumption_hybrid.py
```

필요:

```text
SEOUL_OPEN_DATA_API_KEY
```

권장 주기:

```text
약 5분
```

---

# 14. Runtime Data / Volume

FastAPI request path와 collector가 함께 사용하는 mutable snapshot은 production에서 writable volume으로 공유하는 구성을 권장합니다.

주요 runtime 데이터:

```text
data/weather_overview/latest/
data/sports/latest/
data/performance/latest/
data/oa21285/history/
data/oa21285/cache/latest/
data/consumption/realtime/latest/
```

정적 scoring/baseline snapshot:

```text
data/commercial_stores/scoring/
data/consumption/baseline/aligned_normalized/
data/tourism/normalized/
```

FastAPI request path에서는 KOPIS 또는 Playwright를 request-time heavy fallback으로 실행하지 않습니다.

cache miss 또는 stale cache는 source 상태에 반영합니다.

---

# 15. Backend Integration

권장 구조:

```text
Frontend
   ↓
Spring Boot Backend
   ↓
FastAPI AI/Data
```

FastAPI internal URL:

```text
http://ai:8000
```

Spring에서 호출할 AI/Data API:

```text
GET /health

GET /api/v1/weather-feeds
GET /api/v1/weather-feeds/overview
```

FastAPI는 raw Public Feed Schema v1을 반환합니다.

Frontend-facing Spring API에서는 기존 Backend의 `ApiResponse<T>` convention에 맞게 wrapping하는 것을 권장합니다.

예:

```text
Frontend
→ Spring GET /api/v1/weather-feeds
→ FastAPI GET /api/v1/weather-feeds
→ Spring ApiResponse<T>
```

### Backend error 처리

FastAPI:

```text
422 = 잘못된 입력
500 = AI/Data pipeline 내부 오류
```

Spring:

```text
connection failure
read timeout
FastAPI 5xx
```

는 upstream service error로 구분해 처리하는 것을 권장합니다.

v1에서는 동일 요청을 자동 retry하지 않는 방향을 권장합니다.

### Timeout

기존 Spring global RestClient timeout을 변경하지 않고 Weather Feed용 RestClient/configuration을 별도로 두는 것을 권장합니다.

Detail API는 Overview보다 처리 시간이 길 수 있으므로 global 설정과 별도로 충분한 read timeout을 설정해야 합니다.

로컬 QA에서는 Detail이 대체로 수 초 이내, Overview snapshot read는 매우 빠르게 응답했지만 이는 production SLA를 의미하지 않습니다.

---

# 16. Frontend Integration

## 서울 지도

사용:

```http
GET /api/v1/weather-feeds/overview
```

주요 필드:

```text
districts[].district
districts[].opportunity_score
districts[].grade
districts[].emoji
```

지도 색상은 `grade` 기준으로 Frontend에서 매핑할 수 있습니다.

---

## 선택 자치구 Detail

사용:

```http
GET /api/v1/weather-feeds
```

주요 표시 필드:

```text
opportunity_score

market_weather.score
market_weather.grade
market_weather.emoji

indicators.inflow_pressure
indicators.spending_intent
indicators.competition_pressure
indicators.operational_risk

decision_tags

narrative.judgement_sentence
narrative.basis_sentence
narrative.recommended_actions

content.items

data_quality
```

### Content type

Frontend는 다음 type을 지원해야 합니다.

```text
festival
event
performance
sports
```

특히 `event`는 별도 type입니다.

AI/Data API에서는 `video` type을 생성하지 않습니다.

---

## Overview stale 처리

Overview가:

```json
{
  "status": "stale",
  "is_fresh": false,
  "districts": [...]
}
```

를 반환하더라도 district 데이터는 사용할 수 있습니다.

`stale`을 곧바로 `no_data`로 처리하지 않습니다.

파일이 없는 경우의:

```json
{
  "status": "no_data",
  "districts": []
}
```

와 구분해야 합니다.

---

# 17. OpenAI

## API Key가 없는 경우

```text
generation_mode = rule_fallback
```

OpenAI network call 없이 룰 기반 문장을 반환합니다.

## API Key가 있는 경우

```env
OPENAI_API_KEY=...
OPENAI_MODEL=...
```

정상 호출 및 validation 완료 시:

```text
generation_mode = hybrid_llm
```

LLM 사용 여부와 관계없이 다음 값은 변경하지 않습니다.

```text
opportunity_score
market_weather
indicators
decision_tags
```

---

# 18. Test

현재 regression test는 `pytest`를 기준으로 실행합니다.

개발/QA 환경에서는 다음 명령으로 테스트 의존성을 설치합니다.

```bash
python3 -m pip install -r requirements-dev.txt
```

### macOS / Linux

```bash
PYTHONPATH=. pytest -q
python3 -m compileall -q .
git diff --check
```

현재 최종 QA 기준:

```text
250 passed
0 failed
```

환경에 따라 FastAPI/Starlette/anyio TestClient 관련 deprecation warning이 출력될 수 있으며 이는 테스트 실패가 아닙니다.

Dependency integrity 확인:

```bash
python3 -m pip check
```

---

# 19. Windows UTF-8

프로젝트 내부 텍스트 파일 입출력에는 UTF-8 encoding을 명시합니다.

최신 코드에서는 별도의 `PYTHONUTF8=1` 설정 없이 동작하도록 구성되어 있습니다.

이전 환경에서 `cp949` 관련 문제가 발생하는 경우 임시로 다음 설정을 사용할 수 있습니다.

### CMD

```cmd
set PYTHONUTF8=1
```

### PowerShell

```powershell
$env:PYTHONUTF8="1"
```

---

# 20. Handoff 요약

## Backend

필수 확인:

```text
FastAPI internal URL:
http://ai:8000

Detail:
GET /api/v1/weather-feeds

Overview:
GET /api/v1/weather-feeds/overview

Health:
GET /health
```

FastAPI는 raw Schema v1을 반환합니다.

Spring에서는 기존 `ApiResponse<T>` convention에 맞게 Frontend-facing 응답을 구성합니다.

Weather Feed용 dedicated timeout 설정을 권장합니다.

---

## Frontend

지도:

```text
overview.districts
```

선택 구 상세:

```text
detail Feed
```

Content type:

```text
festival
event
performance
sports
```

`event` type 지원이 필요합니다.

`video`는 AI/Data에서 제공하지 않습니다.

Overview가 `stale`이어도 `districts`가 존재하면 표시할 수 있으며 `is_fresh=false`를 freshness 상태로 해석합니다.

---

## Infra

FastAPI:

```text
0.0.0.0:8000
```

동일 Docker network에서 Spring:

```text
http://ai:8000
```

FastAPI는 internal-only 운영을 권장합니다.

collector와 API가 사용하는 runtime snapshot directory는 공유 writable volume으로 구성합니다.

Weather Overview collector는 권장 5분 주기로 실행하며 중복 실행을 방지합니다.
