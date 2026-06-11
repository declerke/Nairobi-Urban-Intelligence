# =============================================================================
# run.ps1 — Nairobi Urban Intelligence full pipeline (PowerShell)
# =============================================================================
# Usage:
#   .\run.ps1
#
# Runs: setup → fetch → spatial analysis → dbt → tests → dashboard
# =============================================================================

$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$VenvDir    = Join-Path $ProjectDir ".venv"
$EnvFile    = Join-Path $ProjectDir ".env"
$PythonExe  = Join-Path $VenvDir "Scripts\python.exe"
$DbtExe     = Join-Path $VenvDir "Scripts\dbt.exe"
$PytestExe  = Join-Path $VenvDir "Scripts\pytest.exe"
$StreamlitExe = Join-Path $VenvDir "Scripts\streamlit.exe"

Write-Host ""
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host "  Nairobi Urban Intelligence Pipeline" -ForegroundColor Cyan
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host "Project: $ProjectDir"
Write-Host ""

# ---------------------------------------------------------------------------
# 1. Virtual environment setup
# ---------------------------------------------------------------------------
Write-Host "[1/6] Setting up Python virtual environment..." -ForegroundColor Yellow

if (-not (Test-Path $VenvDir)) {
    uv venv $VenvDir --python 3.11
    Write-Host "  Created .venv" -ForegroundColor Green
} else {
    Write-Host "  .venv already exists — skipping creation" -ForegroundColor Gray
}

Write-Host "[1/6] Installing dependencies..." -ForegroundColor Yellow
uv pip install -r "$ProjectDir\requirements.txt" --python $PythonExe
Write-Host "  Dependencies installed." -ForegroundColor Green

# ---------------------------------------------------------------------------
# 2. .env setup
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "[2/6] Loading environment variables..." -ForegroundColor Yellow

if (-not (Test-Path $EnvFile)) {
    Copy-Item "$ProjectDir\.env.example" $EnvFile
    Write-Host "  Created .env from .env.example" -ForegroundColor Green
} else {
    Write-Host "  .env exists — using existing config" -ForegroundColor Gray
}

# Load .env into current session
Get-Content $EnvFile | ForEach-Object {
    if ($_ -match '^\s*([^#][^=]+)=(.+)$') {
        $key   = $Matches[1].Trim()
        $value = $Matches[2].Trim().Trim('"').Trim("'")
        [System.Environment]::SetEnvironmentVariable($key, $value, "Process")
    }
}
Write-Host "  Environment variables loaded." -ForegroundColor Green

# ---------------------------------------------------------------------------
# 3. Fetch POIs from OpenStreetMap
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "[3/6] Fetching data from OpenStreetMap (OSMnx)..." -ForegroundColor Yellow
Write-Host "  This step calls the Overpass API — may take 3–10 minutes." -ForegroundColor Gray

$env:PYTHONPATH = "$ProjectDir\src"
& $PythonExe "$ProjectDir\src\fetch_pois.py"
if ($LASTEXITCODE -ne 0) { throw "fetch_pois.py failed with exit code $LASTEXITCODE" }
Write-Host "  Fetch complete." -ForegroundColor Green

# ---------------------------------------------------------------------------
# 4. Spatial analysis
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "[4/6] Running spatial analysis (DBSCAN + distances)..." -ForegroundColor Yellow

& $PythonExe "$ProjectDir\src\spatial_analysis.py"
if ($LASTEXITCODE -ne 0) { throw "spatial_analysis.py failed with exit code $LASTEXITCODE" }
Write-Host "  Spatial analysis complete." -ForegroundColor Green

# ---------------------------------------------------------------------------
# 5. dbt run + test
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "[5/6] Running dbt transformations..." -ForegroundColor Yellow

Push-Location "$ProjectDir\dbt"
try {
    & $DbtExe run   --profiles-dir "$ProjectDir\dbt" --project-dir "$ProjectDir\dbt"
    if ($LASTEXITCODE -ne 0) { throw "dbt run failed" }
    & $DbtExe test  --profiles-dir "$ProjectDir\dbt" --project-dir "$ProjectDir\dbt"
    if ($LASTEXITCODE -ne 0) { throw "dbt test failed" }
} finally {
    Pop-Location
}
Write-Host "  dbt run + test complete." -ForegroundColor Green

# ---------------------------------------------------------------------------
# 6. pytest
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "[6/6] Running pytest..." -ForegroundColor Yellow

$env:PYTHONPATH = "$ProjectDir\src"
& $PytestExe "$ProjectDir\tests\" -v --tb=short
if ($LASTEXITCODE -ne 0) { throw "pytest failed with exit code $LASTEXITCODE" }

Write-Host ""
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host "  Pipeline complete!" -ForegroundColor Green
Write-Host "  Launch dashboard:" -ForegroundColor Cyan
Write-Host "    `$env:PYTHONPATH = '$ProjectDir\src'" -ForegroundColor White
Write-Host "    & '$StreamlitExe' run '$ProjectDir\dashboard\app.py'" -ForegroundColor White
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host ""

# Optionally auto-launch
$AutoLaunch = [System.Environment]::GetEnvironmentVariable("AUTO_LAUNCH", "Process")
if ($AutoLaunch -eq "true") {
    $env:PYTHONPATH = "$ProjectDir\src"
    & $StreamlitExe run "$ProjectDir\dashboard\app.py"
}
