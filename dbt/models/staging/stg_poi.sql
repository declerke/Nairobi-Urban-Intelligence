-- stg_poi.sql
-- Cleans and standardises the raw_pois table from the Python ingestion layer.
-- Exposes amenity type, WKT geometry, cluster label, and distance columns.

WITH source AS (
    SELECT
        poi_id,
        amenity,
        name,
        latitude,
        longitude,
        geometry_wkt,
        cluster_label,
        nearest_hospital_km,
        nearest_school_km,
        nearest_market_km,
        is_underserved
    FROM main.raw_pois
    WHERE amenity IS NOT NULL
      AND latitude  IS NOT NULL
      AND longitude IS NOT NULL
      AND latitude  BETWEEN -1.5 AND 0.0   -- Nairobi bounding box
      AND longitude BETWEEN 36.5 AND 37.2
),

cleaned AS (
    SELECT
        poi_id,
        LOWER(TRIM(amenity))                          AS amenity,
        NULLIF(TRIM(name), '')                        AS poi_name,
        ROUND(latitude,  6)                           AS latitude,
        ROUND(longitude, 6)                           AS longitude,
        geometry_wkt,
        COALESCE(cluster_label, -1)                   AS cluster_label,
        ROUND(nearest_hospital_km, 4)                  AS nearest_hospital_km,
        ROUND(nearest_school_km,   4)                  AS nearest_school_km,
        ROUND(nearest_market_km,   4)                  AS nearest_market_km,
        COALESCE(is_underserved, FALSE)               AS is_underserved
    FROM source
)

SELECT * FROM cleaned
