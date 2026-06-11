"""
conftest.py — pytest configuration for Nairobi Urban Intelligence tests.
Sets DUCKDB_PATH and SERVICE_DESERT_KM env vars so src/ imports work
without a .env file during testing.
"""
import os
import sys
from pathlib import Path

# Ensure src/ is importable from tests/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# Default env vars for testing (safe, in-memory defaults where possible)
os.environ.setdefault("DUCKDB_PATH", "data/nairobi.duckdb")
os.environ.setdefault("GPKG_PATH", "data/nairobi_pois.gpkg")
os.environ.setdefault("PLACE_NAME", "Nairobi, Kenya")
os.environ.setdefault("DBSCAN_EPS", "0.005")
os.environ.setdefault("DBSCAN_MIN_SAMPLES", "3")
os.environ.setdefault("SERVICE_DESERT_KM", "2.0")
os.environ.setdefault("OVERPASS_RATE_LIMIT", "False")
os.environ.setdefault("OVERPASS_MAX_QUERY_AREA_SIZE", "2500000000")
