"""
spatial_analysis.py
===================
Performs DBSCAN clustering on POI locations, computes nearest-facility distances,
and flags service deserts (zones where the nearest hospital > 2 km).
Updates the raw_pois table in DuckDB with cluster labels and distance columns.

Run:
    python src/spatial_analysis.py
"""

from __future__ import annotations

import math
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from shapely.geometry import MultiPoint, Point
from shapely.ops import nearest_points
from sklearn.cluster import DBSCAN

from utils import (
    duckdb_insert_df,
    duckdb_path,
    get_env,
    get_logger,
)

logger = get_logger("spatial_analysis")

# Earth radius in km (used for haversine DBSCAN eps conversion)
EARTH_RADIUS_KM = 6371.0


# ---------------------------------------------------------------------------
# DBSCAN clustering
# ---------------------------------------------------------------------------

def run_dbscan(df: pd.DataFrame) -> pd.DataFrame:
    """
    Run DBSCAN on lat/lon coordinates using haversine distance.

    Parameters
    ----------
    df : DataFrame with 'latitude' and 'longitude' columns (degrees)

    Returns
    -------
    df with added 'cluster_label' column (int, -1 = noise)
    """
    eps_deg = float(get_env("DBSCAN_EPS", "0.005"))
    min_samples = int(get_env("DBSCAN_MIN_SAMPLES", "3"))

    logger.info("Running DBSCAN: eps=%.4f°, min_samples=%d", eps_deg, min_samples)

    # Convert eps from degrees to radians for haversine metric.
    # sklearn's haversine metric expects distances in radians.
    # math.radians(deg) gives the correct radian value (~0.00873 rad for 0.5°).
    eps_rad = math.radians(eps_deg)

    coords_rad = np.radians(df[["latitude", "longitude"]].values)

    db = DBSCAN(
        eps=eps_rad,
        min_samples=min_samples,
        algorithm="ball_tree",
        metric="haversine",
    ).fit(coords_rad)

    df = df.copy()
    df["cluster_label"] = db.labels_

    n_clusters = len(set(db.labels_)) - (1 if -1 in db.labels_ else 0)
    n_noise = int((db.labels_ == -1).sum())
    logger.info(
        "DBSCAN result: %d clusters, %d noise points (of %d total)",
        n_clusters,
        n_noise,
        len(df),
    )
    return df


# ---------------------------------------------------------------------------
# Nearest-facility distance calculation
# ---------------------------------------------------------------------------

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Compute great-circle distance in km between two WGS84 points."""
    r = EARTH_RADIUS_KM
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def nearest_facility_distance(
    row: pd.Series,
    candidates,
) -> float:
    """
    Return the distance in km from row (lat, lon) to the nearest point in candidates.

    Parameters
    ----------
    candidates : MultiPoint or pd.DataFrame
        Either a Shapely MultiPoint, or a DataFrame with 'latitude' / 'longitude' columns.
    Returns float('inf') if candidates is empty or None.
    """
    # Normalise: accept a DataFrame and build a MultiPoint from it.
    if isinstance(candidates, pd.DataFrame):
        if candidates.empty:
            return float("inf")
        mp = MultiPoint(
            list(zip(candidates["longitude"].values, candidates["latitude"].values))
        )
    else:
        mp = candidates

    if mp is None or mp.is_empty:
        return float("inf")

    pt = Point(row["longitude"], row["latitude"])
    # nearest_points returns (nearest_on_a, nearest_on_b); index 1 is the candidate.
    _, nearest_on_candidates = nearest_points(pt, mp)
    return haversine_km(row["latitude"], row["longitude"], nearest_on_candidates.y, nearest_on_candidates.x)


def compute_distances(df: pd.DataFrame) -> pd.DataFrame:
    """
    For each POI, compute distance (km) to nearest hospital, school, and market.
    Also sets is_underserved flag based on SERVICE_DESERT_KM threshold.
    """
    service_desert_km = float(get_env("SERVICE_DESERT_KM", "2.0"))
    logger.info(
        "Computing nearest-facility distances (service desert threshold: %.1f km)…",
        service_desert_km,
    )

    hospitals = df[df["amenity"] == "hospital"][["latitude", "longitude"]]
    schools = df[df["amenity"].isin(["school", "university"])][["latitude", "longitude"]]
    markets = df[df["amenity"] == "market"][["latitude", "longitude"]]

    logger.info(
        "Facility counts — hospitals: %d, schools: %d, markets: %d",
        len(hospitals),
        len(schools),
        len(markets),
    )

    # Build MultiPoint once per facility type so nearest_points doesn't reconstruct it per row.
    def _mp(fac_df: pd.DataFrame) -> MultiPoint:
        return MultiPoint(list(zip(fac_df["longitude"].values, fac_df["latitude"].values)))

    hospital_mp = _mp(hospitals) if not hospitals.empty else MultiPoint()
    school_mp   = _mp(schools)   if not schools.empty   else MultiPoint()
    market_mp   = _mp(markets)   if not markets.empty   else MultiPoint()

    df = df.copy()

    logger.info("Computing distances to nearest hospital…")
    df["nearest_hospital_km"] = df.apply(
        lambda row: nearest_facility_distance(row, hospital_mp), axis=1
    )

    logger.info("Computing distances to nearest school…")
    df["nearest_school_km"] = df.apply(
        lambda row: nearest_facility_distance(row, school_mp), axis=1
    )

    logger.info("Computing distances to nearest market…")
    df["nearest_market_km"] = df.apply(
        lambda row: nearest_facility_distance(row, market_mp), axis=1
    )

    # Flag underserved zones
    df["is_underserved"] = df["nearest_hospital_km"] > service_desert_km

    n_underserved = df["is_underserved"].sum()
    logger.info(
        "Underserved POIs (nearest hospital > %.1f km): %d / %d",
        service_desert_km,
        n_underserved,
        len(df),
    )
    return df


# ---------------------------------------------------------------------------
# Cluster centroids and convex hulls
# ---------------------------------------------------------------------------

def compute_cluster_shapes(df: pd.DataFrame) -> pd.DataFrame:
    """
    For each DBSCAN cluster (excluding noise -1), compute centroid lat/lon
    and the convex hull WKT as a summary table.
    """
    rows = []
    for label in sorted(df["cluster_label"].unique()):
        if label == -1:
            continue
        cluster_pts = df[df["cluster_label"] == label]
        centroid_lat = cluster_pts["latitude"].mean()
        centroid_lon = cluster_pts["longitude"].mean()
        n_pois = len(cluster_pts)
        amenity_counts = cluster_pts["amenity"].value_counts().to_dict()
        dominant_amenity = cluster_pts["amenity"].mode()[0] if n_pois > 0 else None

        # Convex hull
        try:
            mp = MultiPoint(
                list(zip(cluster_pts["longitude"].values, cluster_pts["latitude"].values))
            )
            hull_wkt = mp.convex_hull.wkt
        except Exception:
            hull_wkt = None

        rows.append(
            {
                "cluster_label": int(label),
                "centroid_lat": centroid_lat,
                "centroid_lon": centroid_lon,
                "n_pois": n_pois,
                "dominant_amenity": dominant_amenity,
                "hull_wkt": hull_wkt,
            }
        )

    result = pd.DataFrame(rows)
    logger.info("Cluster shapes computed: %d clusters", len(result))
    return result


# ---------------------------------------------------------------------------
# Write results back to DuckDB
# ---------------------------------------------------------------------------

def update_duckdb(df: pd.DataFrame, cluster_shapes: pd.DataFrame, db_path: Path) -> None:
    """Write cluster labels and distance columns back into DuckDB."""
    logger.info("Updating DuckDB with analysis results: %s", db_path)
    con = duckdb.connect(str(db_path))

    # Update raw_pois with cluster_label and distance columns
    # Use a staging table approach for clean update.
    # Register the DataFrame explicitly so DuckDB resolves it reliably
    # regardless of calling scope.
    analysis_cols = df[
        ["poi_id", "cluster_label", "nearest_hospital_km",
         "nearest_school_km", "nearest_market_km", "is_underserved"]
    ]
    con.execute("DROP TABLE IF EXISTS analysis_staging")
    duckdb_insert_df(con, analysis_cols, "CREATE TABLE analysis_staging AS SELECT * FROM _df_tmp")

    con.execute("""
        UPDATE raw_pois
        SET
            cluster_label       = s.cluster_label,
            nearest_hospital_km = s.nearest_hospital_km,
            nearest_school_km   = s.nearest_school_km,
            nearest_market_km   = s.nearest_market_km,
            is_underserved      = s.is_underserved
        FROM analysis_staging s
        WHERE raw_pois.poi_id = s.poi_id
    """)
    con.execute("DROP TABLE IF EXISTS analysis_staging")

    # Save cluster shapes.
    # Register the DataFrame under an unambiguous view name before creating
    # the persistent table, so DuckDB does not confuse the Python variable
    # with the SQL table of the same name.
    if len(cluster_shapes) > 0:
        con.execute("DROP TABLE IF EXISTS cluster_shapes")
        duckdb_insert_df(con, cluster_shapes, "CREATE TABLE cluster_shapes AS SELECT * FROM _df_tmp")
        logger.info("Saved %d cluster shapes to DuckDB", len(cluster_shapes))

    # Verification query
    result = con.execute("""
        SELECT
            COUNT(*)                                    AS total,
            COUNT(cluster_label)                        AS clustered,
            SUM(CASE WHEN is_underserved THEN 1 END)    AS underserved,
            ROUND(AVG(nearest_hospital_km), 2)          AS avg_hospital_km
        FROM raw_pois
    """).fetchone()

    logger.info(
        "DuckDB updated — total: %d, clustered: %d, underserved: %d, avg_hospital_km: %.2f",
        *result,
    )
    con.close()


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main() -> None:
    db_path = duckdb_path()

    # Load raw POIs from DuckDB
    logger.info("Loading raw_pois from DuckDB…")
    con = duckdb.connect(str(db_path))
    df = con.execute("SELECT * FROM raw_pois").df()
    con.close()

    if df.empty:
        raise RuntimeError(
            "raw_pois table is empty. Run fetch_pois.py first."
        )
    logger.info("Loaded %d POIs from DuckDB", len(df))

    # DBSCAN
    df = run_dbscan(df)

    # Nearest-facility distances
    df = compute_distances(df)

    # Cluster shapes
    cluster_shapes = compute_cluster_shapes(df)

    # Write back
    update_duckdb(df, cluster_shapes, db_path)

    logger.info("=== Spatial Analysis Complete ===")
    logger.info("Clusters: %d", cluster_shapes["cluster_label"].nunique() if len(cluster_shapes) > 0 else 0)
    logger.info(
        "Underserved POIs: %d / %d",
        int(df["is_underserved"].sum()),
        len(df),
    )


if __name__ == "__main__":
    main()
