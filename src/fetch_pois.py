"""
fetch_pois.py
=============
Fetches Points of Interest (POIs) for Nairobi from OpenStreetMap via OSMnx 2.1.0,
saves to GeoPackage, and loads into DuckDB for downstream dbt transformations.

Run:
    python src/fetch_pois.py
"""

from __future__ import annotations

import uuid
from pathlib import Path

import duckdb
import geopandas as gpd
import osmnx as ox
import pandas as pd
from dotenv import load_dotenv
from shapely.geometry import mapping

from utils import (
    VALID_AMENITY_TYPES,
    data_dir,
    duckdb_path,
    get_env,
    get_logger,
    gpkg_path,
    retry_with_backoff,
)

load_dotenv()

logger = get_logger("fetch_pois")


# ---------------------------------------------------------------------------
# OSMnx settings
# ---------------------------------------------------------------------------

def configure_osmnx() -> None:
    """Apply OSMnx global settings from environment."""
    rate_limit_str = get_env("OVERPASS_RATE_LIMIT", "False")
    ox.settings.overpass_rate_limit = rate_limit_str.lower() not in ("false", "0", "no")

    max_area_str = get_env("OVERPASS_MAX_QUERY_AREA_SIZE", "2500000000")
    ox.settings.max_query_area_size = int(max_area_str)

    ox.settings.log_console = False
    ox.settings.use_cache = True
    ox.settings.cache_folder = str(data_dir() / "osmnx_cache")
    logger.info("OSMnx configured: rate_limit=%s, max_area=%s", ox.settings.overpass_rate_limit, ox.settings.max_query_area_size)


# ---------------------------------------------------------------------------
# POI fetch
# ---------------------------------------------------------------------------

def fetch_pois(place_name: str) -> gpd.GeoDataFrame:
    """
    Fetch POIs from Overpass API for the given place.
    Returns a GeoDataFrame with columns: poi_id, amenity, name, geometry.
    """
    tags = {
        "amenity": VALID_AMENITY_TYPES,
    }
    logger.info("Fetching POIs for '%s' with tags: %s", place_name, tags["amenity"])

    def _fetch():
        return ox.features_from_place(place_name, tags=tags)

    gdf_raw: gpd.GeoDataFrame = retry_with_backoff(_fetch, retries=5, base_delay=15.0)
    logger.info("Raw OSMnx result: %d features", len(gdf_raw))

    # Normalise: keep only the amenity column and geometry
    gdf = gdf_raw.copy()
    gdf = gdf.reset_index()

    # Ensure amenity column exists
    if "amenity" not in gdf.columns:
        raise ValueError("No 'amenity' column found in OSMnx result.")

    # Filter to known amenity types
    gdf = gdf[gdf["amenity"].isin(VALID_AMENITY_TYPES)].copy()
    logger.info("After filtering to known amenity types: %d features", len(gdf))

    # Extract name safely
    if "name" not in gdf.columns:
        gdf["name"] = None
    else:
        gdf["name"] = gdf["name"].astype(str).where(gdf["name"].notna(), None)

    # Reproject to WGS84 if needed
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
    elif gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs("EPSG:4326")

    # Convert all geometry to centroids (polygons → point)
    gdf["geometry"] = gdf["geometry"].centroid

    # Generate stable poi_id
    gdf["poi_id"] = [str(uuid.uuid4()) for _ in range(len(gdf))]

    # Keep only essential columns
    gdf = gdf[["poi_id", "amenity", "name", "geometry"]].copy()

    # Extract lat/lon
    gdf["latitude"] = gdf.geometry.y
    gdf["longitude"] = gdf.geometry.x

    logger.info(
        "POI fetch complete: %d records across %d amenity types",
        len(gdf),
        gdf["amenity"].nunique(),
    )
    return gdf


# ---------------------------------------------------------------------------
# Nairobi sub-county boundary fetch
# ---------------------------------------------------------------------------

def fetch_nairobi_boundary(place_name: str) -> gpd.GeoDataFrame:
    """Fetch the admin boundary polygon for Nairobi."""
    logger.info("Fetching Nairobi boundary…")

    def _fetch():
        return ox.geocode_to_gdf(place_name)

    gdf = retry_with_backoff(_fetch, retries=3, base_delay=10.0)
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
    elif gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs("EPSG:4326")

    logger.info("Boundary fetched: %d polygon(s)", len(gdf))
    return gdf


# ---------------------------------------------------------------------------
# Road network fetch
# ---------------------------------------------------------------------------

def fetch_road_network(place_name: str) -> tuple:
    """
    Fetch the drive road network for Nairobi.
    Returns (graph, edges_gdf) where edges_gdf has highway type and geometry.
    """
    logger.info("Fetching road network for '%s' (network_type=drive) …", place_name)

    def _fetch():
        return ox.graph_from_place(place_name, network_type="drive")

    G = retry_with_backoff(_fetch, retries=3, base_delay=20.0)
    edges = ox.graph_to_gdfs(G, nodes=False, edges=True)

    if edges.crs is None:
        edges = edges.set_crs("EPSG:4326")
    elif edges.crs.to_epsg() != 4326:
        edges = edges.to_crs("EPSG:4326")

    edges = edges.reset_index()

    # Normalise highway column
    if "highway" in edges.columns:
        edges["highway"] = edges["highway"].apply(
            lambda x: x[0] if isinstance(x, list) else x
        )
    else:
        edges["highway"] = "unclassified"

    # Keep relevant columns
    keep = ["geometry", "highway", "length"]
    edges = edges[[c for c in keep if c in edges.columns]].copy()
    edges["road_id"] = [str(uuid.uuid4()) for _ in range(len(edges))]

    total_km = edges["length"].sum() / 1000.0 if "length" in edges.columns else 0.0
    logger.info("Road network: %d edges, %.1f km total", len(edges), total_km)
    return G, edges


# ---------------------------------------------------------------------------
# GeoPackage save
# ---------------------------------------------------------------------------

def save_to_gpkg(
    pois: gpd.GeoDataFrame,
    road_edges: gpd.GeoDataFrame,
    boundary: gpd.GeoDataFrame,
    path: Path,
) -> None:
    """Save all layers to a GeoPackage file."""
    logger.info("Saving to GeoPackage: %s", path)
    pois.to_file(str(path), layer="pois", driver="GPKG")
    road_edges.to_file(str(path), layer="road_edges", driver="GPKG")
    boundary.to_file(str(path), layer="boundary", driver="GPKG")
    logger.info("GeoPackage saved.")


# ---------------------------------------------------------------------------
# DuckDB load
# ---------------------------------------------------------------------------

def load_to_duckdb(
    pois: gpd.GeoDataFrame,
    road_edges: gpd.GeoDataFrame,
    db_path: Path,
) -> None:
    """Load POI and road data into DuckDB tables."""
    logger.info("Loading data into DuckDB: %s", db_path)
    con = duckdb.connect(str(db_path))

    # --- raw_pois table ---
    poi_df = pd.DataFrame(
        {
            "poi_id": pois["poi_id"],
            "amenity": pois["amenity"],
            "name": pois["name"].where(pois["name"].notna(), None),
            "latitude": pois["latitude"],
            "longitude": pois["longitude"],
            "geometry_wkt": pois["geometry"].apply(lambda g: g.wkt),
            "cluster_label": None,  # filled in by spatial_analysis.py
            "nearest_hospital_km": None,
            "nearest_school_km": None,
            "nearest_market_km": None,
            "is_underserved": False,
        }
    )

    con.execute("DROP TABLE IF EXISTS raw_pois")
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
    con.execute("INSERT INTO raw_pois SELECT * FROM poi_df")
    logger.info("Inserted %d rows into raw_pois", len(poi_df))

    # --- raw_road_edges table ---
    if len(road_edges) > 0:
        road_df = pd.DataFrame(
            {
                "road_id": road_edges["road_id"],
                "highway": road_edges["highway"].fillna("unclassified"),
                "length_m": road_edges["length"] if "length" in road_edges.columns else 0.0,
                "geometry_wkt": road_edges["geometry"].apply(lambda g: g.wkt),
            }
        )
        con.execute("DROP TABLE IF EXISTS raw_road_edges")
        con.execute("""
            CREATE TABLE raw_road_edges (
                road_id      VARCHAR PRIMARY KEY,
                highway      VARCHAR,
                length_m     DOUBLE,
                geometry_wkt VARCHAR
            )
        """)
        con.execute("INSERT INTO raw_road_edges SELECT * FROM road_df")
        logger.info("Inserted %d rows into raw_road_edges", len(road_df))

    con.close()
    logger.info("DuckDB load complete.")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main() -> None:
    configure_osmnx()
    place_name = get_env("PLACE_NAME", "Nairobi, Kenya")

    # Fetch data
    pois = fetch_pois(place_name)
    boundary = fetch_nairobi_boundary(place_name)
    _G, road_edges = fetch_road_network(place_name)

    # Persist
    save_to_gpkg(pois, road_edges, boundary, gpkg_path())
    load_to_duckdb(pois, road_edges, duckdb_path())

    # Summary
    logger.info("=== Fetch Summary ===")
    for amenity, count in pois["amenity"].value_counts().items():
        logger.info("  %-15s %d", amenity, count)
    logger.info("Total POIs: %d", len(pois))
    logger.info("Road edges: %d", len(road_edges))


if __name__ == "__main__":
    main()
