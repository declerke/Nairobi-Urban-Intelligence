-- stg_clusters.sql
-- Surfaces cluster shape metadata computed by spatial_analysis.py.
-- Falls back gracefully if cluster_shapes table does not yet exist.

WITH source AS (
    SELECT
        cluster_label,
        centroid_lat,
        centroid_lon,
        n_pois,
        dominant_amenity,
        hull_wkt
    FROM main.cluster_shapes
    WHERE cluster_label >= 0   -- exclude DBSCAN noise (-1)
),

cleaned AS (
    SELECT
        cluster_label,
        ROUND(centroid_lat, 6) AS centroid_lat,
        ROUND(centroid_lon, 6) AS centroid_lon,
        n_pois,
        LOWER(TRIM(dominant_amenity)) AS dominant_amenity,
        hull_wkt
    FROM source
)

SELECT * FROM cleaned
