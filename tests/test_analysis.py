"""
test_analysis.py
================
Tests for spatial_analysis.py — verifies DBSCAN clustering,
nearest-facility distance computation, and service desert flag logic.

Run:
    pytest tests/test_analysis.py -v
"""

from __future__ import annotations

import math
import sys
from pathlib import Path
import uuid

import numpy as np
import pandas as pd
import pytest

# Ensure src/ is on the path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from spatial_analysis import (
    compute_distances,
    haversine_km,
    run_dbscan,
    compute_cluster_shapes,
    nearest_facility_distance,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def clustered_poi_df() -> pd.DataFrame:
    """
    DataFrame with two obvious spatial clusters and one isolated noise point.
    Cluster A: hospitals/schools near Nairobi CBD (~-1.286, 36.817)
    Cluster B: markets/banks near Westlands (~-1.268, 36.808)
    Noise: single pharmacy far from both
    """
    rows = [
        # Cluster A — tight group near CBD
        ("h1", "hospital", -1.286, 36.817),
        ("h2", "hospital", -1.287, 36.818),
        ("h3", "hospital", -1.288, 36.816),
        ("s1", "school",   -1.285, 36.819),
        ("s2", "school",   -1.289, 36.815),
        # Cluster B — tight group in Westlands
        ("m1", "market",   -1.268, 36.808),
        ("m2", "market",   -1.269, 36.807),
        ("m3", "market",   -1.267, 36.809),
        ("b1", "bank",     -1.270, 36.806),
        # Noise — isolated point
        ("p1", "pharmacy", -1.350, 36.900),
    ]
    return pd.DataFrame(rows, columns=["poi_id", "amenity", "latitude", "longitude"])


@pytest.fixture()
def distance_poi_df() -> pd.DataFrame:
    """DataFrame where distances can be computed deterministically."""
    rows = [
        # Hospital at origin
        ("hosp1", "hospital", -1.300, 36.820),
        # School 1 km north of hospital
        ("sch1",  "school",   -1.291, 36.820),
        # Market 3 km east — this should be 'underserved' relative to hospital threshold
        ("mkt1",  "market",   -1.300, 36.847),
        # Bank very close to hospital
        ("bnk1",  "bank",     -1.300, 36.821),
        # POI 5 km from hospital — critical desert
        ("pol1",  "police",   -1.300, 36.865),
    ]
    return pd.DataFrame(rows, columns=["poi_id", "amenity", "latitude", "longitude"])


# ---------------------------------------------------------------------------
# Tests — haversine_km
# ---------------------------------------------------------------------------

def test_haversine_same_point_is_zero():
    """Distance from a point to itself must be 0."""
    d = haversine_km(-1.286, 36.817, -1.286, 36.817)
    assert d == pytest.approx(0.0, abs=1e-9)


def test_haversine_known_distance():
    """
    Nairobi CBD to JKIA is ~15 km straight-line.
    Check that haversine returns a plausible value.
    """
    cbd_lat, cbd_lon = -1.286389, 36.817223
    jkia_lat, jkia_lon = -1.319722, 36.925833
    d = haversine_km(cbd_lat, cbd_lon, jkia_lat, jkia_lon)
    assert 10.0 < d < 20.0, f"Expected ~15 km, got {d:.2f}"


def test_haversine_is_non_negative():
    """Haversine distance must always be >= 0."""
    pairs = [
        (-1.3, 36.8, -1.3, 36.9),
        (-1.0, 36.5, -1.5, 37.0),
        (0.0,  36.0,  0.0, 36.0),
    ]
    for lat1, lon1, lat2, lon2 in pairs:
        assert haversine_km(lat1, lon1, lat2, lon2) >= 0.0


# ---------------------------------------------------------------------------
# Tests — DBSCAN
# ---------------------------------------------------------------------------

def test_dbscan_assigns_cluster_labels(clustered_poi_df):
    """DBSCAN must add a 'cluster_label' column."""
    result = run_dbscan(clustered_poi_df)
    assert "cluster_label" in result.columns


def test_dbscan_finds_at_least_one_cluster(clustered_poi_df):
    """At least one non-noise cluster must be identified."""
    result = run_dbscan(clustered_poi_df)
    unique_labels = set(result["cluster_label"]) - {-1}
    assert len(unique_labels) >= 1, "Expected at least 1 cluster"


def test_dbscan_noise_label_is_minus_one(clustered_poi_df):
    """All labels must be -1 (noise) or a non-negative integer."""
    result = run_dbscan(clustered_poi_df)
    for label in result["cluster_label"]:
        assert label >= -1, f"Invalid cluster label: {label}"


def test_dbscan_label_count_matches_row_count(clustered_poi_df):
    """cluster_label column length must match input DataFrame."""
    result = run_dbscan(clustered_poi_df)
    assert len(result) == len(clustered_poi_df)


# ---------------------------------------------------------------------------
# Tests — nearest_facility_distance
# ---------------------------------------------------------------------------

def test_nearest_facility_distance_is_non_negative(distance_poi_df):
    """Nearest-facility distance must be >= 0 for all rows."""
    hospitals = distance_poi_df[distance_poi_df["amenity"] == "hospital"]
    for _, row in distance_poi_df.iterrows():
        d = nearest_facility_distance(row, hospitals)
        assert d >= 0.0, f"Negative distance for row: {row['poi_id']}"


def test_nearest_facility_distance_to_self_is_zero(distance_poi_df):
    """Distance from a facility to itself (in its own subset) must be 0."""
    hospitals = distance_poi_df[distance_poi_df["amenity"] == "hospital"]
    hosp_row = hospitals.iloc[0]
    d = nearest_facility_distance(hosp_row, hospitals)
    assert d == pytest.approx(0.0, abs=1e-6)


def test_nearest_facility_distance_empty_facilities(distance_poi_df):
    """Distance to empty facility set must be infinity."""
    empty_df = pd.DataFrame(columns=["latitude", "longitude"])
    row = distance_poi_df.iloc[0]
    d = nearest_facility_distance(row, empty_df)
    assert math.isinf(d)


# ---------------------------------------------------------------------------
# Tests — compute_distances (integration)
# ---------------------------------------------------------------------------

def test_compute_distances_adds_columns(distance_poi_df):
    """compute_distances must add nearest_hospital_km, nearest_school_km, nearest_market_km."""
    result = compute_distances(distance_poi_df)
    for col in ["nearest_hospital_km", "nearest_school_km", "nearest_market_km", "is_underserved"]:
        assert col in result.columns


def test_service_desert_flag_logic(distance_poi_df):
    """
    POIs far from the hospital should be flagged is_underserved=True.
    Using threshold 2.0 km: the police point (~3+ km away) must be flagged.
    """
    import os
    os.environ["SERVICE_DESERT_KM"] = "2.0"
    result = compute_distances(distance_poi_df)
    # Police station is >2 km from the hospital
    police_row = result[result["amenity"] == "police"].iloc[0]
    assert police_row["is_underserved"] is True or bool(police_row["is_underserved"]) == True


def test_hospital_zero_distance_to_itself(distance_poi_df):
    """Hospital's own nearest_hospital_km should be 0 (distance to itself)."""
    import os
    os.environ["SERVICE_DESERT_KM"] = "2.0"
    result = compute_distances(distance_poi_df)
    hosp_row = result[result["amenity"] == "hospital"].iloc[0]
    assert hosp_row["nearest_hospital_km"] == pytest.approx(0.0, abs=1e-6)


# ---------------------------------------------------------------------------
# Tests — cluster shapes
# ---------------------------------------------------------------------------

def test_cluster_shapes_returns_dataframe(clustered_poi_df):
    """compute_cluster_shapes must return a DataFrame."""
    labelled = run_dbscan(clustered_poi_df)
    shapes = compute_cluster_shapes(labelled)
    assert isinstance(shapes, pd.DataFrame)


def test_cluster_shapes_no_noise_cluster(clustered_poi_df):
    """Cluster shapes must not include the noise label -1."""
    labelled = run_dbscan(clustered_poi_df)
    shapes = compute_cluster_shapes(labelled)
    if len(shapes) > 0:
        assert -1 not in shapes["cluster_label"].values
