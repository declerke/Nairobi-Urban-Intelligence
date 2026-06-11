"""
test_fetch.py
=============
Tests for fetch_pois.py — verifies OSMnx data retrieval, GeoDataFrame structure,
and DuckDB table creation.

Run:
    pytest tests/test_fetch.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path
import uuid

import duckdb
import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point

# Ensure src/ is on the path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def minimal_poi_gdf() -> gpd.GeoDataFrame:
    """Minimal in-memory GeoDataFrame mimicking OSMnx output."""
    data = {
        "poi_id": [str(uuid.uuid4()) for _ in range(6)],
        "amenity": ["hospital", "school", "market", "bank", "police", "pharmacy"],
        "name": ["KNH", "Alliance HS", "City Market", "KCB", "Central Police", None],
        "latitude": [-1.30, -1.28, -1.29, -1.27, -1.31, -1.26],
        "longitude": [36.82, 36.81, 36.83, 36.80, 36.84, 36.79],
        "geometry_wkt": [
            "POINT (36.82 -1.30)", "POINT (36.81 -1.28)",
            "POINT (36.83 -1.29)", "POINT (36.80 -1.27)",
            "POINT (36.84 -1.31)", "POINT (36.79 -1.26)",
        ],
    }
    gdf = gpd.GeoDataFrame(
        data,
        geometry=[Point(lon, lat) for lon, lat in zip(data["longitude"], data["latitude"])],
        crs="EPSG:4326",
    )
    return gdf


@pytest.fixture()
def tmp_duckdb(tmp_path) -> Path:
    """Create a temporary DuckDB with raw_pois table."""
    db_path = tmp_path / "test_nairobi.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute("""
        CREATE TABLE raw_pois (
            poi_id            VARCHAR PRIMARY KEY,
            amenity           VARCHAR NOT NULL,
            name              VARCHAR,
            latitude          DOUBLE NOT NULL,
            longitude         DOUBLE NOT NULL,
            geometry_wkt      VARCHAR NOT NULL,
            cluster_label     INTEGER,
            nearest_hospital_km DOUBLE,
            nearest_school_km   DOUBLE,
            nearest_market_km   DOUBLE,
            is_underserved    BOOLEAN DEFAULT FALSE
        )
    """)
    # Insert sample rows
    rows = [
        (str(uuid.uuid4()), "hospital", "KNH",         -1.30, 36.82, "POINT (36.82 -1.30)", None, None, None, None, False),
        (str(uuid.uuid4()), "school",   "Alliance HS",  -1.28, 36.81, "POINT (36.81 -1.28)", None, None, None, None, False),
        (str(uuid.uuid4()), "market",   "City Market",  -1.29, 36.83, "POINT (36.83 -1.29)", None, None, None, None, False),
        (str(uuid.uuid4()), "bank",     "KCB",          -1.27, 36.80, "POINT (36.80 -1.27)", None, None, None, None, False),
        (str(uuid.uuid4()), "police",   "Central",      -1.31, 36.84, "POINT (36.84 -1.31)", None, None, None, None, False),
    ]
    con.executemany(
        "INSERT INTO raw_pois VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows
    )
    con.close()
    return db_path


# ---------------------------------------------------------------------------
# Tests — GeoDataFrame structure
# ---------------------------------------------------------------------------

def test_poi_gdf_has_geometry_column(minimal_poi_gdf):
    """OSMnx result must have a geometry column."""
    assert "geometry" in minimal_poi_gdf.columns


def test_poi_gdf_crs_is_wgs84(minimal_poi_gdf):
    """Geometry must be in WGS84 (EPSG:4326)."""
    assert minimal_poi_gdf.crs is not None
    assert minimal_poi_gdf.crs.to_epsg() == 4326


def test_poi_gdf_has_amenity_column(minimal_poi_gdf):
    """GeoDataFrame must contain an amenity column."""
    assert "amenity" in minimal_poi_gdf.columns


def test_poi_gdf_count_positive(minimal_poi_gdf):
    """GeoDataFrame must contain at least one record."""
    assert len(minimal_poi_gdf) > 0


def test_poi_gdf_lat_lon_range(minimal_poi_gdf):
    """All coordinates must be within Nairobi's rough bounding box."""
    assert minimal_poi_gdf["latitude"].between(-2.0, 0.5).all(), "Latitude out of Nairobi range"
    assert minimal_poi_gdf["longitude"].between(36.5, 37.5).all(), "Longitude out of Nairobi range"


def test_poi_ids_are_unique(minimal_poi_gdf):
    """Each POI must have a unique identifier."""
    assert minimal_poi_gdf["poi_id"].nunique() == len(minimal_poi_gdf)


def test_amenity_values_are_known(minimal_poi_gdf):
    """All amenity values must be from the expected set."""
    from utils import VALID_AMENITY_TYPES
    unknown = set(minimal_poi_gdf["amenity"]) - set(VALID_AMENITY_TYPES)
    assert not unknown, f"Unknown amenity types found: {unknown}"


# ---------------------------------------------------------------------------
# Tests — DuckDB table creation
# ---------------------------------------------------------------------------

def test_duckdb_table_created(tmp_duckdb):
    """raw_pois table must exist in DuckDB after loading."""
    con = duckdb.connect(str(tmp_duckdb), read_only=True)
    tables = con.execute("SHOW TABLES").fetchall()
    table_names = [t[0] for t in tables]
    con.close()
    assert "raw_pois" in table_names


def test_duckdb_poi_count_positive(tmp_duckdb):
    """raw_pois table must contain at least one row."""
    con = duckdb.connect(str(tmp_duckdb), read_only=True)
    count = con.execute("SELECT COUNT(*) FROM raw_pois").fetchone()[0]
    con.close()
    assert count > 0


def test_duckdb_poi_amenity_not_null(tmp_duckdb):
    """No amenity column should be NULL in raw_pois."""
    con = duckdb.connect(str(tmp_duckdb), read_only=True)
    nulls = con.execute("SELECT COUNT(*) FROM raw_pois WHERE amenity IS NULL").fetchone()[0]
    con.close()
    assert nulls == 0


def test_duckdb_schema_has_expected_columns(tmp_duckdb):
    """raw_pois must have all required columns."""
    required_cols = {
        "poi_id", "amenity", "name", "latitude", "longitude",
        "geometry_wkt", "cluster_label", "nearest_hospital_km",
        "nearest_school_km", "nearest_market_km", "is_underserved",
    }
    con = duckdb.connect(str(tmp_duckdb), read_only=True)
    result = con.execute("DESCRIBE raw_pois").fetchall()
    actual_cols = {row[0] for row in result}
    con.close()
    missing = required_cols - actual_cols
    assert not missing, f"Missing columns in raw_pois: {missing}"


def test_duckdb_geometry_wkt_format(tmp_duckdb):
    """geometry_wkt values must start with 'POINT'."""
    con = duckdb.connect(str(tmp_duckdb), read_only=True)
    rows = con.execute("SELECT geometry_wkt FROM raw_pois").fetchall()
    con.close()
    for (wkt,) in rows:
        assert wkt.startswith("POINT"), f"Unexpected WKT format: {wkt}"
