import json
import math
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from django.conf import settings

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "data"
CACHE_FILE_PATH = DATA_DIR / "station_coordinates_cache.json"
CENSUS_FILE_PATH = DATA_DIR / "us_cities_census.json"

# Approximate bounding boxes for all 50 US States + DC: (min_lat, max_lat, min_lon, max_lon)
US_STATE_BOUNDING_BOXES: Dict[str, Tuple[float, float, float, float]] = {
    "AL": (30.14, 35.01, -88.47, -84.89),
    "AK": (51.21, 71.39, -179.15, -129.98),
    "AZ": (31.33, 37.00, -114.82, -109.04),
    "AR": (33.00, 36.50, -94.62, -89.64),
    "CA": (32.53, 42.01, -124.41, -114.13),
    "CO": (36.99, 41.00, -109.06, -102.04),
    "CT": (40.98, 42.05, -73.73, -71.79),
    "DE": (38.45, 39.84, -75.79, -75.05),
    "FL": (24.52, 31.00, -87.63, -80.03),
    "GA": (30.36, 35.00, -85.61, -80.84),
    "HI": (18.91, 22.24, -160.25, -154.81),
    "ID": (41.99, 49.00, -117.24, -111.04),
    "IL": (36.97, 42.51, -91.51, -87.02),
    "IN": (37.77, 41.76, -88.10, -84.78),
    "IA": (40.38, 43.50, -96.64, -90.14),
    "KS": (36.99, 40.00, -102.05, -94.59),
    "KY": (36.50, 39.15, -89.57, -81.96),
    "LA": (28.93, 33.02, -94.04, -88.82),
    "ME": (43.06, 47.46, -71.08, -66.95),
    "MD": (37.91, 39.72, -79.49, -75.05),
    "MA": (41.24, 42.89, -73.51, -69.93),
    "MI": (41.69, 48.31, -90.42, -82.41),
    "MN": (43.50, 49.38, -97.24, -89.49),
    "MS": (30.17, 34.99, -91.65, -88.10),
    "MO": (35.99, 40.61, -95.77, -89.10),
    "MT": (44.36, 49.00, -116.05, -104.04),
    "NE": (40.00, 43.00, -104.05, -95.31),
    "NV": (35.00, 42.00, -120.01, -114.04),
    "NH": (42.70, 45.31, -72.56, -70.70),
    "NJ": (38.93, 41.36, -75.56, -73.89),
    "NM": (31.33, 37.00, -109.05, -103.00),
    "NY": (40.50, 45.02, -79.76, -71.86),
    "NC": (33.84, 36.59, -84.32, -75.46),
    "ND": (45.93, 49.00, -104.05, -96.55),
    "OH": (38.40, 41.98, -84.82, -80.52),
    "OK": (33.62, 37.00, -103.00, -94.43),
    "OR": (41.99, 46.29, -124.57, -116.46),
    "PA": (39.72, 42.27, -80.52, -74.69),
    "RI": (41.15, 42.02, -71.86, -71.12),
    "SC": (32.05, 35.22, -83.35, -78.54),
    "SD": (42.48, 45.94, -104.06, -96.44),
    "TN": (34.98, 36.68, -90.31, -81.65),
    "TX": (25.84, 36.50, -106.65, -93.51),
    "UT": (37.00, 42.00, -114.05, -109.04),
    "VT": (42.73, 45.02, -73.44, -71.50),
    "VA": (36.54, 39.47, -83.68, -75.24),
    "WA": (45.54, 49.00, -124.76, -116.92),
    "WV": (37.20, 40.64, -82.64, -77.72),
    "WI": (42.49, 47.08, -92.89, -86.76),
    "WY": (40.99, 45.01, -111.06, -104.05),
    "DC": (38.79, 38.99, -77.12, -76.91),
}

_lock = threading.Lock()
_CENSUS_CACHE: Optional[Dict[str, Any]] = None
_STATION_CACHE: Optional[Dict[str, Dict[str, Any]]] = None


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate the great-circle distance between two points in miles using the Haversine formula."""
    r_miles = 3958.8  # Earth radius in miles
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    )
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return r_miles * c


def normalize_city_name(city: str) -> str:
    """Normalize city string for reliable fuzzy/census matching."""
    s = city.lower().strip()
    s = re.sub(r"[\.\,\']", "", s)
    s = re.sub(r"\bst\b", "saint", s)
    s = re.sub(r"\bmc\s+", "mc", s)
    s = re.sub(r"\s+", " ", s)
    return s


def validate_coordinates(
    lat: Optional[float],
    lon: Optional[float],
    state: str,
    city_centroid: Optional[Tuple[float, float]] = None,
    max_deviation_miles: float = 45.0
) -> Tuple[bool, str]:
    """
    Validate geographic coordinates:
    1. Check non-null, within valid WGS84 range.
    2. Check coordinates fall inside the designated US state's bounding box.
    3. Check proximity to the expected city centroid if provided.
    """
    if lat is None or lon is None:
        return False, "Coordinates are missing or null."

    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return False, f"Coordinates ({lat}, {lon}) are out of WGS84 physical range."

    state_upper = state.strip().upper()
    if state_upper in US_STATE_BOUNDING_BOXES:
        min_lat, max_lat, min_lon, max_lon = US_STATE_BOUNDING_BOXES[state_upper]
        # Pad bounds slightly by 0.2 degrees (~14 miles) to prevent clipping at state borders
        pad = 0.2
        if not (min_lat - pad <= lat <= max_lat + pad and min_lon - pad <= lon <= max_lon + pad):
            return (
                False,
                f"Coordinates ({lat:.4f}, {lon:.4f}) fall outside state {state_upper} boundaries.",
            )

    if city_centroid is not None:
        c_lat, c_lon = city_centroid
        dist = haversine_distance(lat, lon, c_lat, c_lon)
        if dist > max_deviation_miles:
            return (
                False,
                f"Coordinates are {dist:.1f} miles away from city centroid (> {max_deviation_miles} mi threshold).",
            )

    return True, "Valid"


def load_census_cities() -> Dict[str, Any]:
    """Load the US Census Bureau gazetteer place coordinates dictionary."""
    global _CENSUS_CACHE
    with _lock:
        if _CENSUS_CACHE is not None:
            return _CENSUS_CACHE

        if not CENSUS_FILE_PATH.exists():
            _CENSUS_CACHE = {}
            return _CENSUS_CACHE

        try:
            with open(CENSUS_FILE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                _CENSUS_CACHE = data
                return _CENSUS_CACHE
        except Exception:
            _CENSUS_CACHE = {}
            return _CENSUS_CACHE


def get_city_centroid(city: str, state: str) -> Optional[Tuple[float, float]]:
    """Look up city centroid coordinates from the official US Census Gazetteer dataset."""
    census_data = load_census_cities()
    st = state.strip().upper()
    c_norm = normalize_city_name(city)

    key = f"{st}:{c_norm}"
    if key in census_data:
        return float(census_data[key]["lat"]), float(census_data[key]["lon"])

    # Try variants: without spaces, without suffixes
    key_ns = f"{st}:{c_norm.replace(' ', '')}"
    if key_ns in census_data:
        return float(census_data[key_ns]["lat"]), float(census_data[key_ns]["lon"])

    for suffix in [" city", " town", " village", " cdp", " township"]:
        if c_norm.endswith(suffix):
            base = c_norm[:-len(suffix)].strip()
            k = f"{st}:{base}"
            if k in census_data:
                return float(census_data[k]["lat"]), float(census_data[k]["lon"])

    # Check hyphenated city
    if "-" in c_norm:
        first = c_norm.split("-")[0].strip()
        k = f"{st}:{first}"
        if k in census_data:
            return float(census_data[k]["lat"]), float(census_data[k]["lon"])

    return None


def get_station_cache_key(station_id: Any, address: str, city: str, state: str) -> str:
    """Generate a unique deterministic key for caching a station's coordinate data."""
    return f"{station_id}_{address.strip()}_{city.strip()}_{state.strip()}".lower()


def load_persistent_station_cache() -> Dict[str, Dict[str, Any]]:
    """Load persistent station coordinates cache from file or database."""
    global _STATION_CACHE
    with _lock:
        if _STATION_CACHE is not None:
            return _STATION_CACHE

        cache: Dict[str, Dict[str, Any]] = {}

        # 1. Load from JSON cache file if present
        if CACHE_FILE_PATH.exists():
            try:
                with open(CACHE_FILE_PATH, "r", encoding="utf-8") as f:
                    cache = json.load(f)
            except Exception:
                cache = {}

        _STATION_CACHE = cache
        return _STATION_CACHE


def save_persistent_station_cache(cache: Dict[str, Dict[str, Any]]) -> None:
    """Save persistent station coordinates cache to disk."""
    global _STATION_CACHE
    with _lock:
        _STATION_CACHE = cache
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(CACHE_FILE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2)


def enrich_station_record(station: Dict[str, Any]) -> Dict[str, Any]:
    """
    Enrich a single station dictionary with verified or approximate coordinates.
    Never fabricates coordinates or mislabels city centroids as exact.
    """
    # If already enriched with valid coordinates, preserve them!
    if (
        station.get("latitude") is not None
        and station.get("longitude") is not None
        and station.get("status") in ("verified", "approximate")
    ):
        return station

    cache = load_persistent_station_cache()
    key = get_station_cache_key(
        station.get("OPIS Truckstop ID", ""),
        station.get("Address", ""),
        station.get("City", ""),
        station.get("State", ""),
    )

    # Check cache first
    if key in cache:
        cached_entry = cache[key]
        return {**station, **cached_entry}

    city = station.get("City", "")
    state = station.get("State", "")

    # Look up official city centroid from US Census Gazetteer
    centroid = get_city_centroid(city, state)

    if centroid:
        lat, lon = centroid
        is_valid, note = validate_coordinates(lat, lon, state)
        if is_valid:
            entry = {
                "latitude": lat,
                "longitude": lon,
                "confidence": "city_centroid",
                "source": "us_census_gazetteer",
                "is_exact": False,
                "status": "approximate",
                "validation_notes": "Official US Census Bureau city centroid",
            }
            cache[key] = entry
            return {**station, **entry}

    # If completely unresolved
    unresolved_entry = {
        "latitude": None,
        "longitude": None,
        "confidence": "unresolved",
        "source": "none",
        "is_exact": False,
        "status": "unresolved",
        "validation_notes": f"Could not resolve coordinates for {city}, {state}",
    }
    cache[key] = unresolved_entry
    return {**station, **unresolved_entry}


def enrich_all_stations(stations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Enrich a full list of stations with coordinates and metadata."""
    return [enrich_station_record(s) for s in stations]
