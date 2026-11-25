"""Location model used by coordinate/radius market pipelines."""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class LocationContext:
    place_name: str
    latitude: float
    longitude: float
    radius_m: int = 300
    district: str | None = None
    administrative_dong: str | None = None
    road_address: str | None = None
    administrative_dong_code: str | None = None

    def __post_init__(self) -> None:
        if not (-90 <= self.latitude <= 90 and -180 <= self.longitude <= 180):
            raise ValueError("invalid latitude/longitude")
        if self.radius_m <= 0:
            raise ValueError("radius_m must be positive")

    def to_dict(self) -> dict:
        return {**asdict(self), "spatial_scope": "circle_around_anchor_not_entire_street",
                "coordinate_crs": "EPSG:4326"}


# Stable demo anchor. NAVER is used to verify/resolve it when credentials exist;
# absence of credentials is reported and never replaced with a fabricated lookup.
HONGDAE_DEFAULT = LocationContext(
    place_name="홍대입구 거리",
    latitude=37.5563,
    longitude=126.9236,
    radius_m=300,
    district="마포구",
    administrative_dong="서교동",
    administrative_dong_code="1144066000",
    road_address="서울특별시 마포구 양화로 156",
)
