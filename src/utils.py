"""Utility helpers for Nairobi Urban Intelligence pipeline."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def get_logger(name: str) -> logging.Logger:
    """Return a consistently formatted logger."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            "%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def get_env(key: str, default: str | None = None) -> str:
    """Read an env variable; raise if missing and no default provided."""
    value = os.getenv(key, default)
    if value is None:
        raise EnvironmentError(
            f"Required environment variable '{key}' is not set. "
            "Copy .env.example to .env and fill in values."
        )
    return value


def project_root() -> Path:
    """Return the absolute project root directory."""
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    """Return the data/ directory, creating it if needed."""
    d = project_root() / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def duckdb_path() -> Path:
    """Return the DuckDB file path from env config."""
    rel = get_env("DUCKDB_PATH", "data/nairobi.duckdb")
    p = project_root() / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def gpkg_path() -> Path:
    """Return the GeoPackage file path from env config."""
    rel = get_env("GPKG_PATH", "data/nairobi_pois.gpkg")
    p = project_root() / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


# ---------------------------------------------------------------------------
# Retry helper for Overpass rate limits
# ---------------------------------------------------------------------------

def retry_with_backoff(fn, retries: int = 5, base_delay: float = 10.0):
    """Call fn(); on failure retry with exponential backoff."""
    logger = get_logger("utils.retry")
    for attempt in range(retries):
        try:
            return fn()
        except Exception as exc:
            if attempt == retries - 1:
                raise
            wait = base_delay * (2 ** attempt)
            logger.warning(
                "Attempt %d/%d failed: %s. Retrying in %.0fs …",
                attempt + 1,
                retries,
                exc,
                wait,
            )
            time.sleep(wait)


# ---------------------------------------------------------------------------
# Amenity colour map (shared between dashboard and analysis)
# ---------------------------------------------------------------------------

AMENITY_COLOURS: dict[str, str] = {
    "hospital": "red",
    "clinic": "pink",
    "school": "blue",
    "university": "darkblue",
    "bank": "orange",
    "police": "purple",
    "market": "green",
    "pharmacy": "cadetblue",
}

AMENITY_ICONS: dict[str, str] = {
    "hospital": "plus-sign",
    "clinic": "heart",
    "school": "book",
    "university": "education",
    "bank": "usd",
    "police": "star",
    "market": "shopping-cart",
    "pharmacy": "tint",
}

VALID_AMENITY_TYPES: list[str] = list(AMENITY_COLOURS.keys())
