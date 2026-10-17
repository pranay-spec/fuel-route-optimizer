import threading
from pathlib import Path
from typing import Any, Dict, List, Optional
import pandas as pd

# Locate the project directory.
BASE_DIR = Path(__file__).resolve().parent.parent

# Locate the supplied fuel-price CSV.
DEFAULT_CSV_PATH = BASE_DIR / "data" / "fuel-prices-for-be-assessment.csv"

# Expected schema columns in fuel station CSV.
REQUIRED_COLUMNS = [
    "OPIS Truckstop ID",
    "Truckstop Name",
    "Address",
    "City",
    "State",
    "Rack ID",
    "Retail Price",
]

# Supported US 50 States and District of Columbia.
US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE",
    "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS",
    "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS",
    "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY",
    "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC",
}

# Module-level thread-safe in-memory cache.
_cache_lock = threading.Lock()
_CACHED_STATIONS: Optional[List[Dict[str, Any]]] = None
_CACHED_DF: Optional[pd.DataFrame] = None


def clear_fuel_stations_cache() -> None:
    """Clear in-memory cached stations (primarily used during testing)."""
    global _CACHED_STATIONS, _CACHED_DF
    with _cache_lock:
        _CACHED_STATIONS = None
        _CACHED_DF = None


def clean_fuel_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """
    Validate, clean, and normalize fuel price DataFrame according to requirements:
    - Verifies required columns exist.
    - Strips whitespace on text fields.
    - Drops exact duplicate rows only.
    - Normalizes State to uppercase and filters for valid US states.
    - Converts Retail Price to numeric and removes invalid, non-positive, or missing prices.
    - Preserves distinct stations even if they share an OPIS ID or name.
    """
    # 1. Validate required columns.
    missing_cols = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    if missing_cols:
        raise ValueError(f"Fuel CSV missing required column(s): {', '.join(missing_cols)}")

    # 2. Drop exact duplicate records only.
    df_clean = df.drop_duplicates().copy()

    # 3. Strip whitespace on string columns.
    string_cols = ["Truckstop Name", "Address", "City", "State"]
    for col in string_cols:
        df_clean[col] = df_clean[col].astype(str).str.strip()

    # 4. Normalize State and filter for US states only.
    df_clean["State"] = df_clean["State"].str.upper()
    df_clean = df_clean[df_clean["State"].isin(US_STATES)].copy()

    # 5. Convert Retail Price to numeric and exclude missing or non-positive prices.
    df_clean["Retail Price"] = pd.to_numeric(df_clean["Retail Price"], errors="coerce")
    df_clean = df_clean[
        df_clean["Retail Price"].notna() & (df_clean["Retail Price"] > 0)
    ].copy()

    return df_clean


def load_fuel_stations(
    csv_path: Optional[Path | str] = None,
    force_reload: bool = False
) -> List[Dict[str, Any]]:
    """
    Load and clean the fuel-price dataset.
    Uses in-memory caching to avoid repeated disk reads during requests.
    """
    global _CACHED_STATIONS, _CACHED_DF

    if not force_reload and csv_path is None:
        with _cache_lock:
            if _CACHED_STATIONS is not None:
                return _CACHED_STATIONS

    target_path = Path(csv_path) if csv_path else DEFAULT_CSV_PATH

    if not target_path.exists():
        raise FileNotFoundError(f"Fuel-price CSV not found at: {target_path}")

    raw_df = pd.read_csv(target_path)
    clean_df = clean_fuel_dataframe(raw_df)
    records = clean_df.to_dict(orient="records")

    # Cache if loading default dataset
    if csv_path is None:
        with _cache_lock:
            _CACHED_STATIONS = records
            _CACHED_DF = clean_df

    return records


def get_fuel_stations_dataframe(
    csv_path: Optional[Path | str] = None,
    force_reload: bool = False
) -> pd.DataFrame:
    """Return cleaned fuel stations as a pandas DataFrame."""
    global _CACHED_DF
    if not force_reload and csv_path is None and _CACHED_DF is not None:
        return _CACHED_DF.copy()

    records = load_fuel_stations(csv_path=csv_path, force_reload=force_reload)
    return pd.DataFrame(records)
