-- poi_summary.sql
-- Aggregates POI counts by amenity type and DBSCAN cluster.
-- Used by the Cluster Analysis tab in the Streamlit dashboard.

WITH poi AS (
    SELECT * FROM {{ ref('stg_poi') }}
),

cluster_meta AS (
    SELECT * FROM {{ ref('stg_clusters') }}
),

summary AS (
    SELECT
        p.cluster_label,
        p.amenity,
        COUNT(*)                        AS poi_count,
        ROUND(AVG(p.nearest_hospital_km), 3) AS avg_hospital_km,
        ROUND(AVG(p.nearest_school_km),   3) AS avg_school_km,
        ROUND(AVG(p.nearest_market_km),   3) AS avg_market_km,
        SUM(CASE WHEN p.is_underserved THEN 1 ELSE 0 END) AS underserved_count
    FROM poi p
    GROUP BY p.cluster_label, p.amenity
),

enriched AS (
    SELECT
        s.cluster_label,
        s.amenity,
        s.poi_count,
        s.avg_hospital_km,
        s.avg_school_km,
        s.avg_market_km,
        s.underserved_count,
        c.centroid_lat,
        c.centroid_lon,
        c.n_pois                         AS cluster_total_pois,
        c.dominant_amenity
    FROM summary s
    LEFT JOIN cluster_meta c USING (cluster_label)
)

SELECT * FROM enriched
ORDER BY cluster_label, amenity
