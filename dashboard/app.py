"""
app.py — Nairobi Urban Intelligence Dashboard
=============================================
Streamlit + Folium multi-layer map for exploring Nairobi POI data,
service deserts, DBSCAN clusters, and road network.

Run:
    streamlit run dashboard/app.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure src/ is importable when running from project root or dashboard/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import duckdb
import folium
import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from streamlit_folium import st_folium

from utils import AMENITY_COLOURS, AMENITY_ICONS, duckdb_path, get_env

load_dotenv()

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Nairobi Urban Intelligence",
    page_icon="🗺️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Custom CSS
# ---------------------------------------------------------------------------

st.markdown(
    """
    <style>
    .main-header {
        font-size: 2rem;
        font-weight: 700;
        color: #1f4e79;
        margin-bottom: 0.2rem;
    }
    .sub-header {
        color: #555;
        font-size: 0.9rem;
        margin-bottom: 1.5rem;
    }
    .metric-card {
        background: #f0f4fa;
        border-radius: 8px;
        padding: 12px 16px;
        text-align: center;
    }
    .metric-value {
        font-size: 1.8rem;
        font-weight: 700;
        color: #1f4e79;
    }
    .metric-label {
        font-size: 0.8rem;
        color: #666;
        margin-top: 2px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Data loading (cached)
# ---------------------------------------------------------------------------

@st.cache_data(ttl=300, show_spinner="Loading POI data…")
def load_pois() -> pd.DataFrame:
    con = duckdb.connect(str(duckdb_path()), read_only=True)
    try:
        df = con.execute("""
            SELECT
                poi_id, amenity, poi_name, latitude, longitude,
                cluster_label, nearest_hospital_km, nearest_school_km,
                nearest_market_km, is_underserved
            FROM main_staging.stg_poi
        """).df()
    except Exception:
        df = con.execute("""
            SELECT
                poi_id, amenity, name AS poi_name, latitude, longitude,
                cluster_label, nearest_hospital_km, nearest_school_km,
                nearest_market_km, is_underserved
            FROM main.raw_pois
            WHERE amenity IS NOT NULL
        """).df()
    finally:
        con.close()
    return df


@st.cache_data(ttl=300, show_spinner="Loading service desert data…")
def load_service_deserts() -> pd.DataFrame:
    con = duckdb.connect(str(duckdb_path()), read_only=True)
    try:
        df = con.execute("SELECT * FROM main_marts.service_deserts").df()
    except Exception:
        df = con.execute("""
            SELECT
                poi_id, amenity, name AS poi_name, latitude, longitude,
                cluster_label, nearest_hospital_km, nearest_school_km,
                nearest_market_km, is_underserved,
                CASE
                    WHEN nearest_hospital_km > 5.0  THEN 'critical'
                    WHEN nearest_hospital_km > 2.0  THEN 'underserved'
                    ELSE 'served'
                END AS service_level,
                CASE
                    WHEN nearest_hospital_km > 5.0  THEN '#d73027'
                    WHEN nearest_hospital_km > 2.0  THEN '#fc8d59'
                    ELSE '#1a9850'
                END AS colour_hex
            FROM main.raw_pois
            WHERE amenity IS NOT NULL
            ORDER BY nearest_hospital_km DESC
        """).df()
    finally:
        con.close()
    return df


@st.cache_data(ttl=300, show_spinner="Loading cluster data…")
def load_clusters() -> pd.DataFrame:
    con = duckdb.connect(str(duckdb_path()), read_only=True)
    try:
        df = con.execute("SELECT * FROM main_marts.poi_summary").df()
    except Exception:
        try:
            df = con.execute("SELECT * FROM main.cluster_shapes").df()
        except Exception:
            df = pd.DataFrame()
    finally:
        con.close()
    return df


@st.cache_data(ttl=300, show_spinner="Loading road network…")
def load_roads() -> pd.DataFrame:
    con = duckdb.connect(str(duckdb_path()), read_only=True)
    try:
        df = con.execute("""
            SELECT road_id, highway, length_m, geometry_wkt
            FROM main.raw_road_edges
            LIMIT 5000
        """).df()
    except Exception:
        df = pd.DataFrame()
    finally:
        con.close()
    return df


# ---------------------------------------------------------------------------
# Helper: Nairobi centre
# ---------------------------------------------------------------------------

NAIROBI_LAT = -1.286389
NAIROBI_LON = 36.817223


def base_map(zoom: int = 12) -> folium.Map:
    return folium.Map(
        location=[NAIROBI_LAT, NAIROBI_LON],
        zoom_start=zoom,
        tiles="CartoDB positron",
        control_scale=True,
    )


# ---------------------------------------------------------------------------
# Tab 1 — POI Map
# ---------------------------------------------------------------------------

def render_poi_map(pois: pd.DataFrame, selected_amenities: list[str]) -> None:
    st.subheader("Point of Interest Map")
    st.caption("Color-coded markers by amenity type. Use the sidebar to filter.")

    filtered = pois[pois["amenity"].isin(selected_amenities)] if selected_amenities else pois

    m = base_map()

    # One FeatureGroup per amenity type (enables layer toggle)
    groups: dict[str, folium.FeatureGroup] = {}
    for amenity in filtered["amenity"].unique():
        fg = folium.FeatureGroup(name=amenity.capitalize(), show=True)
        groups[amenity] = fg

    for _, row in filtered.iterrows():
        amenity = row["amenity"]
        colour = AMENITY_COLOURS.get(amenity, "gray")
        icon_name = AMENITY_ICONS.get(amenity, "info-sign")
        popup_html = f"""
        <b>{amenity.capitalize()}</b><br>
        {row.get('poi_name') or '<i>unnamed</i>'}<br>
        <small>Lat: {row['latitude']:.5f}, Lon: {row['longitude']:.5f}</small><br>
        <small>Nearest hospital: {row['nearest_hospital_km']:.2f} km</small>
        """
        folium.Marker(
            location=[row["latitude"], row["longitude"]],
            popup=folium.Popup(popup_html, max_width=220),
            icon=folium.Icon(color=colour, icon=icon_name, prefix="glyphicon"),
        ).add_to(groups[amenity])

    for fg in groups.values():
        fg.add_to(m)

    folium.LayerControl(collapsed=False).add_to(m)
    st_folium(m, width="100%", height=600, returned_objects=[])

    # Stats
    col1, col2, col3 = st.columns(3)
    col1.metric("POIs shown", len(filtered))
    col2.metric("Amenity types", filtered["amenity"].nunique())
    col3.metric(
        "Underserved POIs",
        int(filtered["is_underserved"].sum()) if "is_underserved" in filtered.columns else "–",
    )

    st.subheader("Amenity Breakdown")
    breakdown = (
        filtered["amenity"]
        .value_counts()
        .reset_index()
        .rename(columns={"amenity": "Amenity", "count": "Count"})
    )
    st.dataframe(breakdown, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Tab 2 — Service Deserts
# ---------------------------------------------------------------------------

def render_service_deserts(deserts: pd.DataFrame) -> None:
    st.subheader("Service Desert Analysis")
    st.caption(
        "Zones where the nearest hospital exceeds 2 km are flagged as underserved. "
        "Critical zones exceed 5 km."
    )

    # KPI row
    total = len(deserts)
    served = int((deserts["service_level"] == "served").sum()) if "service_level" in deserts.columns else 0
    underserved = int((deserts["service_level"] == "underserved").sum()) if "service_level" in deserts.columns else 0
    critical = int((deserts["service_level"] == "critical").sum()) if "service_level" in deserts.columns else 0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total POIs", total)
    c2.metric("Served (< 2 km)", served, delta=None)
    c3.metric("Underserved (2-5 km)", underserved, delta=f"-{underserved}", delta_color="inverse")
    c4.metric("Critical (> 5 km)", critical, delta=f"-{critical}", delta_color="inverse")

    # Map
    m = base_map()

    colour_map = {
        "served": "green",
        "underserved": "orange",
        "critical": "red",
    }

    for _, row in deserts.iterrows():
        service_level = row.get("service_level", "served")
        colour = colour_map.get(service_level, "gray")
        popup_html = f"""
        <b>{row.get('amenity', '').capitalize()}</b><br>
        {row.get('poi_name') or '<i>unnamed</i>'}<br>
        <b>Service level:</b> {service_level}<br>
        <small>Nearest hospital: {row['nearest_hospital_km']:.2f} km</small>
        """
        folium.CircleMarker(
            location=[row["latitude"], row["longitude"]],
            radius=6,
            color=colour,
            fill=True,
            fill_opacity=0.7,
            popup=folium.Popup(popup_html, max_width=200),
        ).add_to(m)

    # Legend
    legend_html = """
    <div style="position:fixed;bottom:30px;left:30px;z-index:1000;background:white;
                padding:10px 15px;border-radius:8px;border:1px solid #ccc;font-size:13px;">
        <b>Service Level</b><br>
        <span style="color:green;">&#9679;</span> Served (&lt; 2 km)<br>
        <span style="color:orange;">&#9679;</span> Underserved (2–5 km)<br>
        <span style="color:red;">&#9679;</span> Critical (&gt; 5 km)
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))
    st_folium(m, width="100%", height=580, returned_objects=[])

    # Distance distribution
    st.subheader("Distance to Nearest Hospital — Distribution")
    if "nearest_hospital_km" in deserts.columns:
        hist_data = deserts[deserts["nearest_hospital_km"] < 9999]["nearest_hospital_km"]
        st.bar_chart(
            hist_data.value_counts(bins=20, sort=False).sort_index().rename("POI count"),
            use_container_width=True,
        )

    # Table
    with st.expander("View underserved POI records"):
        underserved_df = deserts[deserts["is_underserved"] == True][  # noqa: E712
            ["amenity", "poi_name", "latitude", "longitude", "nearest_hospital_km", "service_level"]
        ].sort_values("nearest_hospital_km", ascending=False)
        st.dataframe(underserved_df, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Tab 3 — Cluster Analysis
# ---------------------------------------------------------------------------

def render_cluster_analysis(pois: pd.DataFrame, clusters: pd.DataFrame) -> None:
    st.subheader("DBSCAN Cluster Analysis")
    st.caption(
        "Spatial clusters identified using DBSCAN (eps ≈ 500 m, min_samples=3). "
        "Cluster -1 = noise (isolated POIs)."
    )

    # Map with cluster colour coding
    m = base_map()

    import hashlib

    def cluster_colour(label: int) -> str:
        if label == -1:
            return "gray"
        palette = [
            "red", "blue", "green", "purple", "orange", "darkred",
            "darkblue", "darkgreen", "cadetblue", "pink",
        ]
        return palette[abs(label) % len(palette)]

    # Draw convex hulls for clusters (from cluster_shapes if available)
    if not clusters.empty and "hull_wkt" in clusters.columns:
        from shapely import wkt as shapely_wkt

        seen_clusters: set[int] = set()
        hull_col = "cluster_label" if "cluster_label" in clusters.columns else None

        if hull_col:
            for _, row in clusters.iterrows():
                label = int(row[hull_col])
                if label == -1 or label in seen_clusters:
                    continue
                seen_clusters.add(label)
                if pd.isna(row.get("hull_wkt")):
                    continue
                try:
                    geom = shapely_wkt.loads(row["hull_wkt"])
                    if geom.geom_type == "Polygon":
                        coords = [[y, x] for x, y in geom.exterior.coords]
                        folium.Polygon(
                            locations=coords,
                            color=cluster_colour(label),
                            fill=True,
                            fill_opacity=0.15,
                            weight=2,
                            popup=f"Cluster {label}",
                        ).add_to(m)
                except Exception:
                    pass

    # Draw POI markers
    for _, row in pois.iterrows():
        label = int(row.get("cluster_label", -1)) if pd.notna(row.get("cluster_label")) else -1
        colour = cluster_colour(label)
        folium.CircleMarker(
            location=[row["latitude"], row["longitude"]],
            radius=5,
            color=colour,
            fill=True,
            fill_opacity=0.8,
            popup=f"{row.get('amenity', '')} | Cluster {label}",
        ).add_to(m)

    st_folium(m, width="100%", height=580, returned_objects=[])

    # Cluster statistics table
    st.subheader("Cluster Statistics")

    if not clusters.empty:
        show_cols = [c for c in [
            "cluster_label", "amenity", "poi_count",
            "avg_hospital_km", "avg_school_km", "avg_market_km", "underserved_count",
            "centroid_lat", "centroid_lon"
        ] if c in clusters.columns]
        st.dataframe(clusters[show_cols], use_container_width=True, hide_index=True)
    else:
        # Fall back to live aggregation from pois
        agg = (
            pois.groupby(["cluster_label", "amenity"])
            .agg(
                poi_count=("poi_id", "count"),
                avg_hospital_km=("nearest_hospital_km", "mean"),
            )
            .reset_index()
        )
        agg["avg_hospital_km"] = agg["avg_hospital_km"].round(2)
        st.dataframe(agg, use_container_width=True, hide_index=True)

    # Noise vs clustered summary
    noise_count = int((pois["cluster_label"] == -1).sum()) if "cluster_label" in pois.columns else 0
    clustered_count = len(pois) - noise_count
    col1, col2, col3 = st.columns(3)
    col1.metric("Total clusters", pois["cluster_label"].nunique() - (1 if -1 in pois["cluster_label"].values else 0) if "cluster_label" in pois.columns else 0)
    col2.metric("Clustered POIs", clustered_count)
    col3.metric("Noise POIs", noise_count)


# ---------------------------------------------------------------------------
# Tab 4 — Road Network
# ---------------------------------------------------------------------------

def render_road_network(roads: pd.DataFrame) -> None:
    st.subheader("Road Network Analysis")
    st.caption("Drive network for Nairobi coloured by road classification.")

    if roads.empty:
        st.info("Road network data not yet loaded. Run fetch_pois.py to fetch road data.")
        return

    # Road type colour map
    highway_colours: dict[str, str] = {
        "motorway": "#e31a1c",
        "trunk": "#fd8d3c",
        "primary": "#fecc5c",
        "secondary": "#a1dab4",
        "tertiary": "#41b6c4",
        "residential": "#225ea8",
        "unclassified": "#aaaaaa",
        "service": "#cccccc",
    }

    m = base_map(zoom=12)

    from shapely import wkt as shapely_wkt

    for _, row in roads.iterrows():
        highway = str(row.get("highway", "unclassified"))
        colour = highway_colours.get(highway, "#888888")
        if pd.isna(row.get("geometry_wkt")):
            continue
        try:
            geom = shapely_wkt.loads(row["geometry_wkt"])
            if geom.geom_type == "LineString":
                coords = [[y, x] for x, y in geom.coords]
            elif geom.geom_type == "MultiLineString":
                coords = [[y, x] for line in geom.geoms for x, y in line.coords]
            else:
                continue
            folium.PolyLine(
                locations=coords,
                color=colour,
                weight=2,
                opacity=0.7,
                tooltip=highway,
            ).add_to(m)
        except Exception:
            continue

    # Road legend
    legend_items = "".join(
        f'<span style="color:{c};">&#9644;</span> {h.capitalize()}<br>'
        for h, c in list(highway_colours.items())[:6]
    )
    legend_html = f"""
    <div style="position:fixed;bottom:30px;left:30px;z-index:1000;background:white;
                padding:10px 15px;border-radius:8px;border:1px solid #ccc;font-size:13px;">
        <b>Road Type</b><br>{legend_items}
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))

    st_folium(m, width="100%", height=580, returned_objects=[])

    # Road stats
    st.subheader("Road Statistics")
    if "highway" in roads.columns:
        road_stats = (
            roads.groupby("highway")
            .agg(
                road_segments=("road_id", "count"),
                total_km=("length_m", lambda x: round(x.sum() / 1000, 1)),
            )
            .reset_index()
            .sort_values("total_km", ascending=False)
        )
        st.dataframe(road_stats, use_container_width=True, hide_index=True)

    total_km = roads["length_m"].sum() / 1000 if "length_m" in roads.columns else 0
    col1, col2 = st.columns(2)
    col1.metric("Total road segments", len(roads))
    col2.metric("Total road length (km)", f"{total_km:.1f}")


# ---------------------------------------------------------------------------
# Main app
# ---------------------------------------------------------------------------

def main() -> None:
    # Header
    st.markdown('<div class="main-header">Nairobi Urban Intelligence</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="sub-header">Geospatial analytics for Nairobi — POI mapping, service deserts, DBSCAN clusters, road network</div>',
        unsafe_allow_html=True,
    )

    # Check DuckDB exists
    db = duckdb_path()
    if not db.exists():
        st.error(
            f"DuckDB database not found at `{db}`. "
            "Run `python src/fetch_pois.py && python src/spatial_analysis.py` first."
        )
        st.stop()

    # Load data
    try:
        pois = load_pois()
        service_deserts = load_service_deserts()
        clusters = load_clusters()
        roads = load_roads()
    except Exception as e:
        st.error(f"Failed to load data: {e}")
        st.info("Make sure the pipeline has been run: `python src/fetch_pois.py && python src/spatial_analysis.py && dbt run`")
        st.stop()

    if pois.empty:
        st.warning("No POI data found. Run the pipeline first.")
        st.stop()

    # Sidebar
    with st.sidebar:
        st.title("Filters")
        st.markdown("---")

        available_amenities = sorted(pois["amenity"].dropna().unique().tolist())
        selected_amenities = st.multiselect(
            "Amenity types",
            options=available_amenities,
            default=available_amenities,
        )

        available_clusters = sorted(pois["cluster_label"].dropna().unique().tolist()) if "cluster_label" in pois.columns else []
        selected_clusters = st.multiselect(
            "Cluster labels (for POI map)",
            options=available_clusters,
            default=available_clusters,
        )

        st.markdown("---")
        st.markdown("### Dataset Summary")
        st.metric("Total POIs", len(pois))
        st.metric("Amenity types", pois["amenity"].nunique())
        if "cluster_label" in pois.columns:
            n_clusters = pois["cluster_label"].nunique() - (1 if -1 in pois["cluster_label"].values else 0)
            st.metric("DBSCAN clusters", n_clusters)
        if "is_underserved" in pois.columns:
            st.metric("Underserved zones", int(pois["is_underserved"].sum()))

        st.markdown("---")
        st.markdown(
            "<small>Data: OpenStreetMap via OSMnx 2.1.0<br>"
            "Analysis: DBSCAN spatial clustering<br>"
            "DB: DuckDB + dbt-duckdb</small>",
            unsafe_allow_html=True,
        )

    # Filter pois by selected options
    filtered_pois = pois.copy()
    if selected_amenities:
        filtered_pois = filtered_pois[filtered_pois["amenity"].isin(selected_amenities)]
    if selected_clusters and "cluster_label" in filtered_pois.columns:
        filtered_pois = filtered_pois[filtered_pois["cluster_label"].isin(selected_clusters)]

    # Tabs
    tab1, tab2, tab3, tab4 = st.tabs(
        ["POI Map", "Service Deserts", "Cluster Analysis", "Road Network"]
    )

    with tab1:
        render_poi_map(filtered_pois, selected_amenities)

    with tab2:
        render_service_deserts(service_deserts)

    with tab3:
        render_cluster_analysis(pois, clusters)

    with tab4:
        render_road_network(roads)


if __name__ == "__main__":
    main()
