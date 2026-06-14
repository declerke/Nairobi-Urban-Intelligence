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
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv
from streamlit_folium import st_folium

from utils import AMENITY_COLOURS, AMENITY_ICONS, duckdb_path, format_km

load_dotenv()

# ---------------------------------------------------------------------------
# Colour constants
# ---------------------------------------------------------------------------

BG     = "#060b17"   # page background
CARD   = "#0d1929"   # sidebar + metric cards
ACCENT = "#00d26a"   # green accent (tabs, metric values, highlighted numbers)
GOLD   = "#f5a623"   # warnings / secondary highlights
TEXT   = "#e2e8f0"   # main text
MUTED  = "#8896a5"   # captions, labels
BLUE   = "#4299e1"   # info / road colours

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

st.markdown(f"""
<style>
html, body, [data-testid="stAppViewContainer"], .main {{
    background-color: {BG} !important; color: {TEXT} !important;
}}
[data-testid="stSidebar"] {{ background-color: {CARD} !important; }}
[data-testid="collapsedControl"],[data-testid="stSidebarCollapseButton"],
[data-testid="stSidebarCollapsedControl"],button[aria-label="Close sidebar"],
button[aria-label="Open sidebar"] {{ display: none !important; }}
span.material-symbols-rounded,span.material-symbols-outlined,span.material-icons {{
    visibility: hidden !important; font-size: 0 !important;
}}
[data-testid="stTabs"] button {{ color: {MUTED} !important; border-bottom: 2px solid transparent; }}
[data-testid="stTabs"] button[aria-selected="true"] {{
    color: {ACCENT} !important; border-bottom: 2px solid {ACCENT} !important;
}}
[data-testid="stMetric"] {{
    background: {CARD} !important; border: 1px solid #1e2d3d !important;
    border-radius: 8px !important; padding: 16px 20px !important;
}}
[data-testid="stMetricValue"] {{ color: {ACCENT} !important; font-size: 1.8rem !important; }}
[data-testid="stMetricLabel"] {{ color: {MUTED} !important; }}
[data-testid="stDataFrame"] {{ background: {CARD} !important; }}
h1, h2, h3, h4 {{ color: {TEXT} !important; }}
hr {{ border-color: #1e2d3d !important; }}
.stTabs [data-baseweb="tab-panel"] {{ background: {BG} !important; }}
[data-testid="stExpander"] {{ background: {CARD} !important; border: 1px solid #1e2d3d !important; border-radius: 8px !important; }}
</style>
""", unsafe_allow_html=True)

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


def render_map(m: folium.Map, height: int = 580) -> None:
    st_folium(m, width=None, height=height, returned_objects=[], use_container_width=True)


# ---------------------------------------------------------------------------
# Helper: styled section header
# ---------------------------------------------------------------------------

def section_header(title: str, subtitle: str = "") -> None:
    subtitle_html = (
        f"<div style='color:{MUTED};font-size:0.85rem;margin-top:2px;'>{subtitle}</div>"
        if subtitle else ""
    )
    st.markdown(f"""
    <div style="margin-bottom:12px;">
      <div style="font-size:1.25rem;font-weight:700;color:{TEXT};">{title}</div>
      {subtitle_html}
    </div>
    """, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Tab 1 — POI Map
# ---------------------------------------------------------------------------

def render_poi_map(pois: pd.DataFrame, selected_amenities: list[str]) -> None:
    section_header(
        "Point of Interest Map",
        "Color-coded markers by amenity type. Use the sidebar to filter."
    )

    if pois.empty:
        st.info("No POI data available. Run the pipeline first.")
        return

    filtered = pois[pois["amenity"].isin(selected_amenities)] if selected_amenities else pois

    m = base_map()

    for amenity in filtered["amenity"].unique():
        subset = filtered[filtered["amenity"] == amenity]
        colour = AMENITY_COLOURS.get(amenity, "gray")
        features = [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [r.longitude, r.latitude]},
                "properties": {
                    "name": r.poi_name or "unnamed",
                    "hosp": f"{r.nearest_hospital_km:.1f} km" if pd.notna(r.nearest_hospital_km) else "N/A",
                },
            }
            for r in subset.itertuples(index=False)
        ]
        folium.GeoJson(
            {"type": "FeatureCollection", "features": features},
            name=amenity.capitalize(),
            marker=folium.CircleMarker(
                radius=6,
                color=colour,
                fill=True,
                fill_color=colour,
                fill_opacity=0.85,
                weight=1,
            ),
            tooltip=folium.GeoJsonTooltip(
                fields=["name", "hosp"],
                aliases=["POI:", "Nearest hospital:"],
            ),
        ).add_to(m)

    folium.LayerControl(collapsed=False).add_to(m)
    render_map(m, height=600)

    # Stats row
    col1, col2, col3 = st.columns(3)
    col1.metric("POIs shown", len(filtered))
    col2.metric("Amenity types", filtered["amenity"].nunique())
    col3.metric(
        "Underserved POIs",
        int(filtered["is_underserved"].sum()) if "is_underserved" in filtered.columns else "–",
    )

    # Plotly amenity breakdown
    section_header("Amenity Breakdown")
    breakdown = filtered["amenity"].value_counts().reset_index()
    breakdown.columns = ["Amenity", "Count"]
    fig = px.bar(
        breakdown,
        x="Count",
        y="Amenity",
        orientation="h",
        color="Count",
        color_continuous_scale=[CARD, ACCENT],
        template="plotly_dark",
    )
    fig.update_layout(
        paper_bgcolor=BG,
        plot_bgcolor=CARD,
        showlegend=False,
        coloraxis_showscale=False,
        height=max(250, len(breakdown) * 35),
        margin=dict(l=0, r=0, t=10, b=0),
    )
    st.plotly_chart(fig, use_container_width=True)


# ---------------------------------------------------------------------------
# Tab 2 — Service Deserts
# ---------------------------------------------------------------------------

def render_service_deserts(deserts: pd.DataFrame) -> None:
    section_header(
        "Service Desert Analysis",
        "Zones where the nearest hospital exceeds 2 km are flagged as underserved. "
        "Critical zones exceed 5 km.",
    )

    if deserts.empty:
        st.info("No service desert data available. Run the pipeline first.")
        return

    # KPI row
    total = len(deserts)
    served = int((deserts["service_level"] == "served").sum()) if "service_level" in deserts.columns else 0
    underserved = int((deserts["service_level"] == "underserved").sum()) if "service_level" in deserts.columns else 0
    critical = int((deserts["service_level"] == "critical").sum()) if "service_level" in deserts.columns else 0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total POIs", total)
    c2.metric("Served (< 2 km)", served)
    c3.metric("Underserved (2–5 km)", underserved, delta=f"-{underserved}", delta_color="inverse")
    c4.metric("Critical (> 5 km)", critical, delta=f"-{critical}", delta_color="inverse")

    # Map — GeoJSON grouped by service_level
    m = base_map()

    for level, colour in [("served", "#1a9850"), ("underserved", "#fc8d59"), ("critical", "#d73027")]:
        subset = deserts[deserts["service_level"] == level] if "service_level" in deserts.columns else pd.DataFrame()
        if subset.empty:
            continue
        features = [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [r.longitude, r.latitude]},
                "properties": {
                    "amenity": (r.amenity or "").capitalize(),
                    "name": r.poi_name or "unnamed",
                    "level": level,
                    "hosp": f"{r.nearest_hospital_km:.1f} km" if pd.notna(r.nearest_hospital_km) else "N/A",
                },
            }
            for r in subset.itertuples(index=False)
        ]
        folium.GeoJson(
            {"type": "FeatureCollection", "features": features},
            name=level.capitalize(),
            marker=folium.CircleMarker(
                radius=6,
                color=colour,
                fill=True,
                fill_color=colour,
                fill_opacity=0.75,
                weight=1,
            ),
            tooltip=folium.GeoJsonTooltip(
                fields=["amenity", "name", "level", "hosp"],
                aliases=["Type:", "POI:", "Service level:", "Nearest hospital:"],
            ),
        ).add_to(m)

    # Legend (dark-themed)
    legend_html = f"""
    <div style="position:fixed;bottom:30px;left:30px;z-index:1000;
                background:{CARD};padding:12px 16px;border-radius:8px;
                border:1px solid #1e2d3d;font-size:13px;color:{TEXT};">
        <b style="color:{TEXT};">Service Level</b><br>
        <span style="color:#1a9850;">&#9679;</span> Served (&lt; 2 km)<br>
        <span style="color:#fc8d59;">&#9679;</span> Underserved (2–5 km)<br>
        <span style="color:#d73027;">&#9679;</span> Critical (&gt; 5 km)
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))
    render_map(m)

    # Distance distribution — Plotly histogram
    section_header("Distance to Nearest Hospital — Distribution")
    if "nearest_hospital_km" in deserts.columns:
        fig = px.histogram(
            deserts,
            x="nearest_hospital_km",
            nbins=30,
            color_discrete_sequence=[ACCENT],
            template="plotly_dark",
            labels={"nearest_hospital_km": "Distance to Nearest Hospital (km)"},
        )
        fig.update_layout(
            paper_bgcolor=BG,
            plot_bgcolor=CARD,
            margin=dict(l=0, r=0, t=10, b=0),
            height=280,
        )
        st.plotly_chart(fig, use_container_width=True)

    # Underserved table
    with st.expander("View underserved POI records"):
        underserved_df = deserts[deserts["is_underserved"] == True][  # noqa: E712
            ["amenity", "poi_name", "latitude", "longitude", "nearest_hospital_km", "service_level"]
        ].sort_values("nearest_hospital_km", ascending=False)
        st.dataframe(underserved_df, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Tab 3 — Cluster Analysis
# ---------------------------------------------------------------------------

def render_cluster_analysis(pois: pd.DataFrame, clusters: pd.DataFrame) -> None:
    section_header(
        "DBSCAN Cluster Analysis",
        "Spatial clusters identified using DBSCAN (eps ≈ 500 m, min_samples=3). "
        "Cluster -1 = noise (isolated POIs).",
    )

    if pois.empty:
        st.info("No POI data available. Run the pipeline first.")
        return

    palette = [
        "#e31a1c", "#4299e1", "#00d26a", "#9b59b6", "#f5a623",
        "#1abc9c", "#e74c3c", "#3498db", "#2ecc71", "#8e44ad",
    ]

    def cluster_colour(label: int) -> str:
        return "gray" if label == -1 else palette[abs(label) % len(palette)]

    # Map
    m = base_map()

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

    # GeoJSON grouped by cluster label
    for label in sorted(pois["cluster_label"].dropna().unique()):
        label = int(label)
        colour = cluster_colour(label)
        subset = pois[pois["cluster_label"] == label]
        features = [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [r.longitude, r.latitude]},
                "properties": {
                    "amenity": (r.amenity or "").capitalize(),
                    "cluster": str(label) if label != -1 else "noise",
                },
            }
            for r in subset.itertuples(index=False)
        ]
        folium.GeoJson(
            {"type": "FeatureCollection", "features": features},
            name=f"Cluster {label}" if label != -1 else "Noise",
            marker=folium.CircleMarker(
                radius=5,
                color=colour,
                fill=True,
                fill_color=colour,
                fill_opacity=0.8,
                weight=1,
            ),
            tooltip=folium.GeoJsonTooltip(
                fields=["amenity", "cluster"],
                aliases=["Type:", "Cluster:"],
            ),
        ).add_to(m)

    render_map(m)

    # Noise vs clustered metrics
    noise_count = int((pois["cluster_label"] == -1).sum()) if "cluster_label" in pois.columns else 0
    clustered_count = len(pois) - noise_count
    n_clusters = (
        pois["cluster_label"].nunique() - (1 if -1 in pois["cluster_label"].values else 0)
        if "cluster_label" in pois.columns else 0
    )

    col1, col2, col3 = st.columns(3)
    col1.metric("Total clusters", n_clusters)
    col2.metric("Clustered POIs", clustered_count)
    col3.metric("Noise POIs", noise_count)

    # Plotly donut — clustered vs noise
    if "cluster_label" in pois.columns:
        fig = px.pie(
            values=[clustered_count, noise_count],
            names=["Clustered", "Noise (isolated)"],
            color_discrete_sequence=[ACCENT, MUTED],
            template="plotly_dark",
            hole=0.5,
        )
        fig.update_layout(
            paper_bgcolor=BG,
            height=280,
            margin=dict(l=0, r=0, t=20, b=0),
        )
        st.plotly_chart(fig, use_container_width=True)

    # Cluster statistics table
    section_header("Cluster Statistics")

    if not clusters.empty:
        show_cols = [c for c in [
            "cluster_label", "amenity", "poi_count",
            "avg_hospital_km", "avg_school_km", "avg_market_km", "underserved_count",
            "centroid_lat", "centroid_lon",
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


# ---------------------------------------------------------------------------
# Tab 4 — Road Network
# ---------------------------------------------------------------------------

def render_road_network(roads: pd.DataFrame) -> None:
    section_header(
        "Road Network Analysis",
        "Drive network for Nairobi coloured by road classification.",
    )

    if roads.empty:
        st.info("Road network data not yet loaded. Run fetch_pois.py to fetch road data.")
        return

    # Road type colour map
    highway_colours: dict[str, str] = {
        "motorway":      "#e31a1c",
        "trunk":         "#fd8d3c",
        "primary":       "#fecc5c",
        "secondary":     "#a1dab4",
        "tertiary":      "#41b6c4",
        "residential":   "#225ea8",
        "unclassified":  "#aaaaaa",
        "service":       "#cccccc",
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

    # Road legend (dark-themed)
    legend_items = "".join(
        f'<span style="color:{c};">&#9644;</span> {h.capitalize()}<br>'
        for h, c in list(highway_colours.items())[:6]
    )
    legend_html = f"""
    <div style="position:fixed;bottom:30px;left:30px;z-index:1000;
                background:{CARD};padding:12px 16px;border-radius:8px;
                border:1px solid #1e2d3d;font-size:13px;color:{TEXT};">
        <b style="color:{TEXT};">Road Type</b><br>{legend_items}
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))

    render_map(m)

    # Road stats — Plotly horizontal bar
    section_header("Road Statistics")
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
        fig = px.bar(
            road_stats.head(8),
            x="total_km",
            y="highway",
            orientation="h",
            color="total_km",
            color_continuous_scale=[CARD, BLUE],
            template="plotly_dark",
        )
        fig.update_layout(
            paper_bgcolor=BG,
            plot_bgcolor=CARD,
            coloraxis_showscale=False,
            height=300,
            margin=dict(l=0, r=0, t=10, b=0),
        )
        st.plotly_chart(fig, use_container_width=True)

    total_km = roads["length_m"].sum() / 1000 if "length_m" in roads.columns else 0
    col1, col2 = st.columns(2)
    col1.metric("Total road segments", len(roads))
    col2.metric("Total road length (km)", f"{total_km:.1f}")


# ---------------------------------------------------------------------------
# Main app
# ---------------------------------------------------------------------------

def main() -> None:
    # Check DuckDB exists before loading data so we can show a clear error
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
        st.info(
            "Make sure the pipeline has been run: "
            "`python src/fetch_pois.py && python src/spatial_analysis.py && dbt run`"
        )
        st.stop()

    if pois.empty:
        st.warning("No POI data found. Run the pipeline first.")
        st.stop()

    # Styled banner header (placed after data load so real counts are available)
    st.markdown(f"""
    <div style="background:linear-gradient(135deg,{CARD} 0%,#0a2040 100%);
                border-left:4px solid {ACCENT};border-radius:8px;
                padding:24px 28px;margin-bottom:20px;">
      <div style="font-size:1.9rem;font-weight:800;color:{TEXT};letter-spacing:-0.5px;">
        🗺️ Nairobi Urban Intelligence
      </div>
      <div style="color:{MUTED};font-size:0.95rem;margin-top:6px;">
        Geospatial analytics · {len(pois):,} POIs · DBSCAN clusters · Road network · Service deserts
      </div>
    </div>
    """, unsafe_allow_html=True)

    # Sidebar
    with st.sidebar:
        st.markdown(f"""
        <div style="padding:4px 0 16px;border-bottom:1px solid #1e2d3d;margin-bottom:16px;">
          <div style="font-size:1.1rem;font-weight:700;color:{TEXT};">🗺️ Nairobi UI</div>
          <div style="font-size:0.78rem;color:{MUTED};margin-top:2px;">Urban Intelligence Dashboard</div>
        </div>
        """, unsafe_allow_html=True)

        st.markdown(
            f"<div style='color:{MUTED};font-size:0.8rem;font-weight:600;"
            f"text-transform:uppercase;letter-spacing:1px;margin-bottom:6px;'>Filters</div>",
            unsafe_allow_html=True,
        )

        available_amenities = sorted(pois["amenity"].dropna().unique().tolist())
        selected_amenities = st.multiselect(
            "Amenity types",
            options=available_amenities,
            default=available_amenities,
        )

        available_clusters = (
            sorted(pois["cluster_label"].dropna().unique().tolist())
            if "cluster_label" in pois.columns else []
        )
        selected_clusters = st.multiselect(
            "Cluster labels (for POI map)",
            options=available_clusters,
            default=available_clusters,
        )

        st.markdown("---")
        st.markdown(
            f"<div style='color:{MUTED};font-size:0.8rem;font-weight:600;"
            f"text-transform:uppercase;letter-spacing:1px;margin-bottom:8px;'>Dataset</div>",
            unsafe_allow_html=True,
        )
        st.metric("Total POIs", len(pois))
        st.metric("Amenity types", pois["amenity"].nunique())
        if "cluster_label" in pois.columns:
            n_clusters = pois["cluster_label"].nunique() - (
                1 if -1 in pois["cluster_label"].values else 0
            )
            st.metric("DBSCAN clusters", n_clusters)
        if "is_underserved" in pois.columns:
            st.metric("Underserved zones", int(pois["is_underserved"].sum()))

        st.markdown(f"""
        <div style="margin-top:16px;padding:12px;background:{BG};border-radius:6px;border:1px solid #1e2d3d;">
          <div style="font-size:0.75rem;color:{MUTED};line-height:1.6;">
            📍 Source: OpenStreetMap via OSMnx 2.1.0<br>
            🔬 Analysis: DBSCAN (eps=500m, min=3)<br>
            💾 Pipeline: DuckDB + dbt-duckdb
          </div>
        </div>
        """, unsafe_allow_html=True)

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
