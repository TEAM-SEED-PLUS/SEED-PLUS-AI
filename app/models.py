"""OpenAPI models mirroring Public Feed Schema v1."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class SchemaModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FeedQuery(SchemaModel):
    district: str | None
    date: str | None
    time: str | None
    time_band: str | None


class MarketWeather(SchemaModel):
    score: int | float | None
    grade: str | None
    emoji: str | None


class Indicators(SchemaModel):
    inflow_pressure: int | float | None
    spending_intent: int | float | None
    competition_pressure: int | float | None
    operational_risk: int | float | None


class Narrative(SchemaModel):
    generation_mode: str
    judgement_sentence: str
    basis_sentence: str
    recommended_actions: list[str]


class ScoreContext(SchemaModel):
    basis: str | None = None
    reference_date: str | None = None
    reference_time_band: str | None = None
    reference_start: str | None = None
    reference_end: str | None = None
    representative_time: str | None = None


class DataQuality(SchemaModel):
    status: Literal["ok", "partial", "fallback", "no_data"]
    data_insufficient: bool
    badges: list[str]
    fallback_sources: list[str]
    no_data_sources: list[str]
    stale_sources: list[str]
    skipped_sources: list[str]
    failed_sources: list[str]
    empty_sources: list[str]
    score_context: ScoreContext


class SourceStatus(SchemaModel):
    source: str
    status: Literal["ok", "empty", "partial", "fallback", "no_data", "failed", "complete"]
    item_count: int | None = None
    fallback: bool | None = None
    source_time: str | None = None
    snapshot_count: int | None = None
    requested_month: str | None = None
    source_month: str | None = None
    age_months: int | float | None = None
    mode: str | None = None
    requested_quarter: str | None = None
    source_quarter: str | None = None
    age_quarters: int | float | None = None
    age_minutes: int | float | None = None
    valid_place_count: int | None = None
    eligibility_reason: str | None = None


class ContentSources(SchemaModel):
    festival: SourceStatus
    event: SourceStatus
    performance: SourceStatus
    sports: SourceStatus


class ContentItem(SchemaModel):
    id: str
    type: Literal["festival", "event", "performance", "sports"]
    title: str
    period: str | None
    place: str | None
    thumbnail_url: str | None
    link_url: str | None


class ContentBlock(SchemaModel):
    items: list[ContentItem]


class Sources(SchemaModel):
    weather: SourceStatus
    content: ContentSources
    special_day: SourceStatus
    footfall: SourceStatus
    tourism: SourceStatus
    commercial_store: SourceStatus
    consumption_baseline: SourceStatus
    realtime_commerce: SourceStatus
    competition_sdot: SourceStatus


class PublicFeedV1(SchemaModel):
    schema_version: Literal["1.0"]
    query: FeedQuery
    opportunity_score: int | float | None
    market_weather: MarketWeather
    indicators: Indicators
    decision_tags: list[str]
    narrative: Narrative
    data_quality: DataQuality
    sources: Sources
    content: ContentBlock
    generated_at: str


class OverviewQuery(SchemaModel):
    date: str
    time: str
    time_band: Literal["심야", "아침", "점심", "오후", "저녁"]


class OverviewDistrict(SchemaModel):
    district: str
    opportunity_score: int | float | None
    grade: str | None
    emoji: str | None


class WeatherFeedOverviewResponse(SchemaModel):
    schema_version: Literal["1.0"]
    query: OverviewQuery
    status: Literal["ok", "partial", "failed", "no_data", "stale"]
    districts: list[OverviewDistrict]
    generated_at: str
    source_time: str | None
    age_minutes: int | float | None
    fresh_ttl_minutes: int | float
    is_fresh: bool


class HealthResponse(SchemaModel):
    status: Literal["ok"]
