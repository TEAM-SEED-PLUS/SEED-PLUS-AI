# SEED-PLUS-AI 내부 스케줄러 배포 가이드

FastAPI `app.main:app`의 lifespan에서 날씨와 홍대 스케줄러를 함께 시작합니다. 배치는 스레드에서 실행하고, API는 원자적으로 교체된 JSON을 읽습니다. 종료 시 진행 중인 스레드를 기다립니다. 기존 Docker 명령은 Uvicorn worker 1개이며 별도 Gunicorn 설정은 없습니다.

| 작업 | 서울 시간 | 범위 |
| --- | --- | --- |
| 날씨 overview | 시작 시 실행 자격 확인, 이후 5분 간격 | overview 스냅샷만 오늘 포함 7일 보존 |
| 홍대 수집 및 일별 분석 | 매일 15:30 | D−4 및 최근 누락 재확인, 입력 해시 변경 시 재계산 |
| 홍대 주간 발행 | 목요일 15:30, 일별 작업 이후 | 지난주 월~일. 누락 시 partial |
| 홍대 재발행 | 영향받는 일별 자료 반영 뒤 | 분석 또는 비교 주간에 해당하는 기존 브리핑. 동일 입력 해시면 생략 |

시각은 `operations_settings.py`에 있습니다. 이전 날씨 스케줄러 환경변수 3개는 더 이상 읽지 않습니다. 실패는 예외 클래스 또는 수집 상태만 기록합니다. HTTP URL과 인증키는 기록하지 않습니다. 로그에서는 `Hongdae collection`, `Hongdae briefing published`, `Hongdae scheduler tick failed`, `Weather overview collector`를 확인합니다.

## 단일 컨테이너와 데이터 마운트

로컬 Compose는 단일 `ai` 컨테이너와 Uvicorn worker 1개를 사용합니다. `WORKDIR /app`, `./data:/app/data` bind mount, `env_file: .env`를 그대로 사용합니다. 같은 호스트 `./data`를 다시 마운트하면 원본과 생성물, 수집 manifest, 실행 상태가 유지됩니다.

SEED-PLUS-INFRA의 AWS 운영 역할은 Ubuntu 24.04 amd64 기반 APP EC2(`t3.small` 기본값)에서 `judemin/seed-plus-ai:latest`를 pull하여 단일 컨테이너를 실행합니다. 운영 데이터는 `seed-plus-ai-data:/app/data` Docker named volume에 남고, 인증정보는 기존 Ansible 컨트롤러 환경변수에서 전달됩니다. 운영 역할이나 Compose를 변경할 필요가 없습니다. 기존 운영 volume의 실측 UID·모드는 이 저장소에서 확인할 수 없습니다.

이미지는 빌드 시점의 초기 자료를 별도 `/opt/seed-data`에 포함합니다. 시작 시 `docker_seed_data.py`가 `app` 사용자 권한으로 런타임 `/app/data`에 없는 파일만 원자적으로 복사한 뒤 Uvicorn으로 전환합니다. `/data`는 `/app/data`의 별칭입니다. 기존 호스트 파일은 보존합니다. 임시 파일·잠금·스케줄러 실행 상태와 보존 정책에 따라 만료되는 날씨 overview 캐시는 이미지에서 제외합니다.

빈 bind mount에도 이미지에 포함된 자료가 자동 준비됩니다. 호스트 `./data`는 컨테이너 사용자가 쓸 수 있어야 하며 초기화 실패 시 Uvicorn을 시작하지 않습니다. 기존 경로 환경변수로 경로를 재정의할 때도 컨테이너에서 보이는 `/app/data` 아래 경로를 사용하세요. 이미지 빌드 환경에는 Git LFS 실제 SEMAS ZIP과 Git에 없는 분기 JSON이 필요합니다. 빌드에서 ZIP 형식이 잘못되거나 Git LFS 포인터가 남으면 오류가 발생합니다.

기존 외부 API 키와 선택 설정은 현재처럼 `.env`에서 주입합니다. 날씨 overview의 `WEATHER_OVERVIEW_SCHEDULER_ENABLED`, `WEATHER_OVERVIEW_INTERVAL_MINUTES`, `WEATHER_OVERVIEW_RETENTION_DAYS`는 주입이 필요 없습니다. 값은 코드에서 활성화·5분·7일로 관리합니다.

컨테이너 실행 사용자 `app`이 호스트 `./data`에 디렉터리와 파일을 생성·교체할 수 있어야 합니다. 이미지의 소유권은 bind mount 권한을 바꾸지 않습니다. 운영 담당자가 실행 중인 컨테이너에서 다음 점검을 수행할 수 있습니다.

```bash
docker compose exec ai python -c 'import tempfile; from pathlib import Path; from snapshot_cache import write_snapshot, read_snapshot; root=Path("/app/data"); assert root.is_dir(); t=tempfile.TemporaryDirectory(dir=root); p=Path(t.name)/"probe.json"; write_snapshot(p, {"ok": True}); assert read_snapshot(p)=={"ok": True}; t.cleanup()'
```

날씨와 홍대는 별도 POSIX 파일 잠금을 사용합니다. 단일 worker 안에서도 자동 홍대 배치와 수동 backfill/CLI가 겹칠 수 있어, 기존 `data/hongdae/.population.lock`을 계속 사용합니다. 운영 점검에서는 같은 `./data`를 연결해 컨테이너를 재생성한 뒤 기존 브리핑과 `scheduler_state.json`을 다시 읽을 수 있는지 확인하세요.

실행 상태에는 마지막 성공 슬롯, 마지막 발행 분석 기간, 재발행 대기 주간과 스냅샷 입력 해시가 남습니다. 상태가 있는 재시작은 놓친 슬롯을 복구합니다. 첫 시작에서는 최신 도래 슬롯부터 확인합니다. 미공개 날짜는 수집 manifest에 남고, 수집 장애는 같은 슬롯에서 재시도합니다. 자동 발행은 LLM을 호출하지 않습니다. 이미 생성된 JSON은 개별 파일 단위로 원자 교체되며, 일별 5개 파일 전체를 묶는 디스크 트랜잭션은 제공하지 않습니다.

## 이미지 초기 자료 및 런타임 생성 파일

초기 자료는 이미지 빌드 시점에 준비합니다. 아래 표의 선적재 안내는 이미지에 해당 자료가 없거나 호스트에 과거 실행 이력을 이전하는 경우에도 적용됩니다.

| 상대 경로 / 실제 파일 예 | 출처·기준 | 초기 적재와 갱신 | 누락 시 |
| --- | --- | --- | --- |
| `data/raw/living_population/250_LOCAL_RESD_20261102.zip`~`250_LOCAL_RESD_20261121.zip` | 서울시·KT OA-22784, 파일명 관측일 | 11/16~22 브리핑과 전주 비교를 재생성하려면 11/09~21 ZIP 선적재. 11/09~15 브리핑의 전주까지 비교하려면 11/02~08도 선적재. 11/22 미확보. 최근 날짜는 HTTPS 일별 수집 | 미관측 날짜는 partial, retry |
| `data/raw/spatial/seoul-grid-250m-5179.zip` | 서울시 공식 EPSG:5179 250m 격자, 배포된 SHP 버전 | 반경 생활인구에 필수, Git 원본에서 수동 적재. 서울시 격자 개정 시 교체 | 행정동 단위 fallback |
| `data/raw/spatial/서울시 상권분석서비스(영역-행정동).zip` | 서울시 행정동 경계 | 현재 분석 코드의 필수 입력 아님 | 영향 없음 |
| `data/hongdae/quarterly/sales_20262.json`, `stores_20262.json` | 서울시 OA-22175/OA-22172, 2026Q2 | 서교동 실데이터 브리핑에 선적재. 승인된 분기 수집 CLI로 갱신 | 매출 no_data, 점포는 기존 아카이브 fallback 가능 |
| `data/risk/seoul_closure/source/OA-22172_2025.zip`, `OA-22172_2026Q1.jsonl` | 서울시 과거 점포·개폐업 | 분기 fallback이 필요할 때 기존 추적 파일에서 적재 | fallback 미제공 |
| `data/raw/stores/소상공인시장진흥공단_상가(상권)정보_20260930.zip` | SEMAS, 2026-09-30 | 주변 경쟁정보에 선적재. Git LFS 실제 ZIP 필요. 새 공표 시 수동 교체 | 구조적 반경 점포 no_data |
| `data/hongdae/backfill/YYYY-MM-DD_<시간대>.json` | 앱 일별 분석 | 자동 생성. 기존 이력 이전 시 복사 | 원본 백필 전 주간 집계 누락 |
| `data/hongdae/weekly/YYYY-MM-DD_YYYY-MM-DD.json` | 앱 주간 브리핑 | 자동 생성. 즉시 조회가 필요하면 예시 저장본 선적재 | 미발행 HTTP 404 |
| `data/hongdae/population_collection.json`, `data/hongdae/scheduler_state.json` | 앱 manifest 및 실행 상태 | 자동 생성. 재배포 시 유지 | 저장된 실행 이력 및 재시도 추적이 초기화 |
| `data/weather_overview/latest/`, `data/weather_overview/.collector-state.json` | 앱 날씨 overview | 자동 생성. 기존 캐시 선적재 가능 | 첫 갱신 전 자료 없음 |

수동 복구 명령은 `python3 -m hongdae_jobs publish --start YYYY-MM-DD`입니다. 소급 수정 원본은 `--refresh YYYY-MM-DD`를 추가합니다. 분기 갱신은 `python3 -m hongdae_quarterly_collect`로 분리합니다. 서울시 분기 API의 HTTPS 제약과 인증키 전송 정책을 확인한 뒤 실행하세요.

2026Q2 분기 JSON 두 개는 해당 경로만 Git LFS로 관리합니다. 출처는 서울신용보증재단의 [OA-22175 추정매출](https://data.seoul.go.kr/dataList/OA-22175/S/1/datasetView.do) 및 [OA-22172 점포](https://data.seoul.go.kr/dataList/OA-22172/S/1/datasetView.do)입니다. 두 데이터셋의 이용허락범위는 제한 없음으로 표시됩니다. 새 체크아웃에서는 `git lfs pull`로 실제 파일을 받은 뒤 `docker buildx build --platform linux/amd64 --load -t judemin/seed-plus-ai:<release> .`로 빌드합니다. 필수 데이터가 빠졌거나 Git LFS 포인터가 남으면 빌드가 실패합니다. 데이터가 들어 있는 이미지를 배포하면 운영 호스트에서 별도로 초기 자료를 적재할 필요가 없습니다.
