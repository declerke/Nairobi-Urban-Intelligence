#!/usr/bin/env bash
# =============================================================================
# run.sh — Nairobi Urban Intelligence full pipeline (bash)
# =============================================================================
# Usage:
#   chmod +x run.sh
#   ./run.sh
#
# Runs: setup → fetch → spatial analysis → dbt → tests → dashboard
# =============================================================================

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${PROJECT_DIR}/.venv"
ENV_FILE="${PROJECT_DIR}/.env"

echo ""
echo "============================================="
echo "  Nairobi Urban Intelligence Pipeline"
echo "============================================="
echo "Project: ${PROJECT_DIR}"
echo ""

# ---------------------------------------------------------------------------
# 1. Environment setup
# ---------------------------------------------------------------------------
echo "[1/6] Setting up Python virtual environment..."

if [ ! -d "${VENV_DIR}" ]; then
    uv venv "${VENV_DIR}" --python 3.11
    echo "  Created .venv"
else
    echo "  .venv already exists — skipping creation"
fi

echo "[1/6] Installing dependencies..."
uv pip install -r "${PROJECT_DIR}/requirements.txt" --python "${VENV_DIR}/bin/python"
echo "  Dependencies installed."

# ---------------------------------------------------------------------------
# 2. .env setup
# ---------------------------------------------------------------------------
echo ""
echo "[2/6] Loading environment variables..."

if [ ! -f "${ENV_FILE}" ]; then
    cp "${PROJECT_DIR}/.env.example" "${ENV_FILE}"
    echo "  Created .env from .env.example"
else
    echo "  .env exists — using existing config"
fi

set -a
# shellcheck disable=SC1090
source "${ENV_FILE}"
set +a

# ---------------------------------------------------------------------------
# 3. Fetch POIs and road network from OpenStreetMap
# ---------------------------------------------------------------------------
echo ""
echo "[3/6] Fetching data from OpenStreetMap (OSMnx)..."
echo "  This step calls the Overpass API — may take 3–10 minutes."

PYTHONPATH="${PROJECT_DIR}/src" \
    "${VENV_DIR}/bin/python" "${PROJECT_DIR}/src/fetch_pois.py"

echo "  Fetch complete."

# ---------------------------------------------------------------------------
# 4. Run spatial analysis (DBSCAN + distances)
# ---------------------------------------------------------------------------
echo ""
echo "[4/6] Running spatial analysis (DBSCAN clustering + distance computation)..."

PYTHONPATH="${PROJECT_DIR}/src" \
    "${VENV_DIR}/bin/python" "${PROJECT_DIR}/src/spatial_analysis.py"

echo "  Spatial analysis complete."

# ---------------------------------------------------------------------------
# 5. Run dbt transformations
# ---------------------------------------------------------------------------
echo ""
echo "[5/6] Running dbt transformations..."

# Export an absolute DuckDB path so profiles.yml can resolve it regardless
# of the working directory dbt is invoked from.
REL_DUCKDB="${DUCKDB_PATH:-data/nairobi.duckdb}"
export DUCKDB_PATH_ABS="${PROJECT_DIR}/${REL_DUCKDB}"
echo "  DUCKDB_PATH_ABS=${DUCKDB_PATH_ABS}"

cd "${PROJECT_DIR}/dbt"
"${VENV_DIR}/bin/dbt" run --profiles-dir "${PROJECT_DIR}/dbt" --project-dir "${PROJECT_DIR}/dbt"
"${VENV_DIR}/bin/dbt" test --profiles-dir "${PROJECT_DIR}/dbt" --project-dir "${PROJECT_DIR}/dbt"
cd "${PROJECT_DIR}"

echo "  dbt run + test complete."

# ---------------------------------------------------------------------------
# 6. Run pytest
# ---------------------------------------------------------------------------
echo ""
echo "[6/6] Running pytest..."

PYTHONPATH="${PROJECT_DIR}/src" \
    "${VENV_DIR}/bin/pytest" "${PROJECT_DIR}/tests/" -v --tb=short

echo ""
echo "============================================="
echo "  Pipeline complete!"
echo "  Launch dashboard:"
echo "  PYTHONPATH=${PROJECT_DIR}/src \\"
echo "    ${VENV_DIR}/bin/streamlit run ${PROJECT_DIR}/dashboard/app.py"
echo "============================================="
echo ""

# Optionally auto-launch dashboard
if [ "${AUTO_LAUNCH:-false}" = "true" ]; then
    PYTHONPATH="${PROJECT_DIR}/src" \
        "${VENV_DIR}/bin/streamlit" run "${PROJECT_DIR}/dashboard/app.py"
fi
