-- service_deserts.sql
-- Identifies POIs in service desert zones (nearest hospital > SERVICE_DESERT_KM).
-- Used by the Service Deserts tab in the Streamlit dashboard.

WITH poi AS (
    SELECT * FROM {{ ref('stg_poi') }}
),

deserts AS (
    SELECT
        poi_id,
        amenity,
        poi_name,
        latitude,
        longitude,
        geometry_wkt,
        cluster_label,
        nearest_hospital_km,
        nearest_school_km,
        nearest_market_km,
        is_underserved,
        CASE
            WHEN nearest_hospital_km > 5.0  THEN 'critical'
            WHEN nearest_hospital_km > 2.0  THEN 'underserved'
            ELSE                                 'served'
        END AS service_level,
        CASE
            WHEN nearest_hospital_km > 5.0  THEN '#d73027'
            WHEN nearest_hospital_km > 2.0  THEN '#fc8d59'
            ELSE                                 '#1a9850'
        END AS colour_hex
    FROM poi
)

SELECT * FROM deserts
ORDER BY nearest_hospital_km DESC
