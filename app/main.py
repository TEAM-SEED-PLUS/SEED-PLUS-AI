"""Internal FastAPI boundary for Public Feed Schema v1."""
from __future__ import annotations

from datetime import datetime
import re
from typing import Annotated, Literal

from dotenv import load_dotenv

# common.py captures environment variables during import, so this must remain
# before imports from the existing AI/Data pipeline.
load_dotenv()

from fastapi import FastAPI, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.models import HealthResponse, PublicFeedV1, WeatherFeedOverviewResponse
from common import SEOUL_TZ, resolve_time_input, normalize_district
from public_feed_schema import generate_public_market_feed
from weather_overview_cache import load_weather_overview

app = FastAPI(title="Weather Feed AI/Data API", version="1.0.0")

DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_PATTERN = re.compile(r"^\d{2}:\d{2}$")
TimeBand = Literal["심야", "아침", "점심", "오후", "저녁"]


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_request, _exc: RequestValidationError) -> JSONResponse:
    # FastAPI's default body echoes rejected input. Keep boundary errors stable
    # and avoid reflecting credentials accidentally supplied as query values.
    return JSONResponse(status_code=422, content={"detail": "Invalid request parameters."})


def _validate_district(value: str) -> str:
    try:
        district, _ = normalize_district(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="district must be a valid Seoul district.") from None
    return district


def _validate_date(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        if not DATE_PATTERN.fullmatch(value):
            raise ValueError
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=422, detail="date must use YYYY-MM-DD format.") from None
    return value


def _validate_time(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        if not TIME_PATTERN.fullmatch(value):
            raise ValueError
        datetime.strptime(value, "%H:%M")
    except ValueError:
        raise HTTPException(status_code=422, detail="time must use HH:MM format.") from None
    return value


@app.get("/health", response_model=HealthResponse)
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get(
    "/api/v1/weather-feeds",
    response_model=PublicFeedV1,
    response_model_exclude_unset=True,
)
async def weather_feeds(
    district: Annotated[str, Query(description="서울 25개 자치구명 (예: 강남구)")],
    date: Annotated[str | None, Query(description="조회 날짜 (YYYY-MM-DD)", pattern=r"^\d{4}-\d{2}-\d{2}$")] = None,
    time: Annotated[str | None, Query(description="조회 시각 (HH:MM, Asia/Seoul)", pattern=r"^\d{2}:\d{2}$")] = None,
    time_band: Annotated[TimeBand | None, Query(description="시간대 (심야/아침/점심/오후/저녁)")] = None,
) -> dict:
    validated_district = _validate_district(district)
    validated_date = _validate_date(date)
    validated_time = _validate_time(time)
    if time_band is not None:
        try:
            validated_time, _ = resolve_time_input(validated_time, time_band)
        except ValueError:
            raise HTTPException(status_code=422, detail="time and time_band must agree.") from None
    try:
        return await run_in_threadpool(
            generate_public_market_feed,
            district=validated_district,
            date_str=validated_date,
            time_str=validated_time,
        )
    except Exception:
        # Do not expose upstream response bodies, URLs, keys, or exception text.
        raise HTTPException(status_code=500, detail="Weather feed generation failed.") from None


@app.get("/api/v1/weather-feeds/overview", response_model=WeatherFeedOverviewResponse)
async def weather_feeds_overview(
    date: Annotated[str | None, Query(description="조회 날짜 (YYYY-MM-DD)", pattern=r"^\d{4}-\d{2}-\d{2}$")] = None,
    time: Annotated[str | None, Query(description="조회 시각 (HH:MM, Asia/Seoul)", pattern=r"^\d{2}:\d{2}$")] = None,
    time_band: Annotated[TimeBand | None, Query(description="시간대 (심야/아침/점심/오후/저녁)")] = None,
) -> dict:
    validated_date = _validate_date(date) or datetime.now(SEOUL_TZ).strftime("%Y-%m-%d")
    validated_time = _validate_time(time)
    try:
        resolved_time, resolved_band = resolve_time_input(validated_time, time_band)
    except ValueError:
        raise HTTPException(status_code=422, detail="time and time_band must agree.") from None
    return await run_in_threadpool(load_weather_overview, validated_date,
                                   resolved_time, resolved_band)
