"""Intersect the official Seoul grid with an analysis circle."""
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from snapshot_cache import read_snapshot, write_snapshot


def grid_weights(path, latitude, longitude, radius):
    digest = sha256(Path(path).read_bytes()).hexdigest()
    key = sha256(f"{digest}:CELL_ID:{latitude}:{longitude}:{radius}".encode()).hexdigest()
    cache = Path("data/hongdae/grid") / f"{key}.json"
    if saved := read_snapshot(cache):
        return saved["weights"]
    import shapefile
    from pyproj import CRS, Transformer
    from shapely.geometry import Point, shape

    with ZipFile(path) as source:
        member = next(name[:-4] for name in source.namelist() if name.endswith(".shp"))
        crs = CRS.from_wkt(source.read(member + ".prj").decode("ascii"))
        if crs.to_epsg() != 5179:
            raise ValueError("Unexpected Seoul grid CRS")
        transform = Transformer.from_crs(4326, crs, always_xy=True)
        circle = Point(*transform.transform(longitude, latitude)).buffer(radius, quad_segs=64)
        reader = shapefile.Reader(**{part: BytesIO(source.read(member + "." + part))
                                     for part in ("shp", "shx", "dbf")})
        weights = {}
        for record in reader.iterShapeRecords():
            polygon = shape(record.shape.__geo_interface__)
            if not circle.intersects(polygon):
                continue
            grid = str(record.record["CELL_ID"]).strip()
            if grid in weights or not grid or abs(polygon.area - 62_500) > 1:
                raise ValueError("Invalid official grid geometry or ID")
            overlap = circle.intersection(polygon).area
            if overlap:
                weights[grid] = overlap / polygon.area
        if not weights:
            raise ValueError("No official grids intersect the radius")
    write_snapshot(cache, {"weights": weights, "source_sha256": digest, "crs": "EPSG:5179",
                           "grid_id_field": "CELL_ID"})
    return weights
