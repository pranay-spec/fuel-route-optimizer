import hashlib
import json
import math
import threading
from typing import Any, Dict, List, Optional, Tuple

import requests
from django.conf import settings

BASE_URL = "https://api.heigit.org"

# Thread-safe in-memory cache for geocoding and routing queries
_route_cache_lock = threading.Lock()
_GEOCODE_CACHE: Dict[str, List[float]] = {}
_DIRECTIONS_CACHE: Dict[str, Dict[str, Any]] = {}


def clear_routing_caches() -> None:
    """Clear in-memory routing and geocoding caches (used in tests)."""
    global _GEOCODE_CACHE, _DIRECTIONS_CACHE
    with _route_cache_lock:
        _GEOCODE_CACHE.clear()
        _DIRECTIONS_CACHE.clear()


def _get_api_key() -> str:
    api_key = getattr(settings, "ORS_API_KEY", None)
    if not api_key or not str(api_key).strip() or str(api_key).strip() == "your_openrouteservice_api_key_here":
        raise RuntimeError("ORS_API_KEY is missing or invalid in configuration / .env")
    return str(api_key).strip()


def geocode_location(location: str) -> List[float]:
    """
    Convert a US place name into [longitude, latitude] coordinates.
    Restricts results to the United States.
    Caches results persistently in memory to avoid duplicate API requests.
    """
    clean_location = location.strip()
    if not clean_location:
        raise ValueError("Location string cannot be empty.")

    cache_key = clean_location.lower()
    with _route_cache_lock:
        if cache_key in _GEOCODE_CACHE:
            return _GEOCODE_CACHE[cache_key]

    api_key = _get_api_key()

    try:
        response = requests.get(
            f"{BASE_URL}/pelias/v1/search",
            params={
                "text": clean_location,
                "size": 1,
                "boundary.country": "USA",
            },
            headers={
                "Authorization": api_key,
                "Accept": "application/json",
            },
            timeout=15,
        )
    except requests.exceptions.Timeout:
        raise requests.exceptions.RequestException("Geocoding service timed out after 15 seconds.")
    except requests.exceptions.RequestException as err:
        raise requests.exceptions.RequestException(f"Network error connecting to geocoding service: {err}")

    if response.status_code == 401 or response.status_code == 403:
        raise RuntimeError("Geocoding service rejected the API key (401/403 Unauthorized).")
    if response.status_code == 429:
        raise requests.exceptions.RequestException("Geocoding service rate limit exceeded (HTTP 429).")
    if response.status_code >= 500:
        raise requests.exceptions.RequestException(f"Geocoding upstream service error: HTTP {response.status_code}")

    response.raise_for_status()
    data = response.json()

    features = data.get("features", [])
    if not features:
        raise ValueError(f"Could not find a valid US location for: '{location}'")

    feature = features[0]
    coordinates = feature["geometry"]["coordinates"]

    # Verify coordinates are [lon, lat]
    lon, lat = coordinates[0], coordinates[1]
    if not (-180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0):
        raise ValueError(f"Geocoding returned invalid coordinate pair: {coordinates}")

    with _route_cache_lock:
        _GEOCODE_CACHE[cache_key] = coordinates

    return coordinates


def get_driving_route(
    start_coordinates: List[float],
    end_coordinates: List[float]
) -> Dict[str, Any]:
    """
    Retrieve a driving route between two coordinate pairs from OpenRouteService.
    Returns:
    - distance_miles (float)
    - duration_seconds (int)
    - geometry (GeoJSON LineString dict)
    """
    # Cache key based on rounded coordinates to 5 decimal places (~1m)
    cache_key = f"{start_coordinates[0]:.5f},{start_coordinates[1]:.5f}->{end_coordinates[0]:.5f},{end_coordinates[1]:.5f}"

    with _route_cache_lock:
        if cache_key in _DIRECTIONS_CACHE:
            return _DIRECTIONS_CACHE[cache_key]

    api_key = _get_api_key()

    try:
        response = requests.post(
            f"{BASE_URL}/openrouteservice/v2/directions/driving-car/geojson",
            json={
                "coordinates": [
                    start_coordinates,
                    end_coordinates,
                ]
            },
            headers={
                "Authorization": api_key,
                "Content-Type": "application/json",
                "Accept": "application/geo+json, application/json, */*",
            },
            timeout=30,
        )
    except requests.exceptions.Timeout:
        raise requests.exceptions.RequestException("Routing service timed out after 30 seconds.")
    except requests.exceptions.RequestException as err:
        raise requests.exceptions.RequestException(f"Network error connecting to routing service: {err}")

    if response.status_code == 401 or response.status_code == 403:
        raise RuntimeError("Routing service rejected the API key (401/403 Unauthorized).")
    if response.status_code == 429:
        raise requests.exceptions.RequestException("Routing service rate limit exceeded (HTTP 429).")
    if response.status_code >= 500:
        raise requests.exceptions.RequestException(f"Routing upstream service error: HTTP {response.status_code}")

    response.raise_for_status()
    data = response.json()

    features = data.get("features", [])
    if not features:
        raise ValueError("The routing service returned no route for the specified coordinates.")

    feature = features[0]
    properties = feature.get("properties", {})
    geometry = feature.get("geometry", {})

    summary = properties.get("summary", {})
    if "distance" not in summary or "duration" not in summary:
        raise ValueError("The routing service response missing summary distance/duration.")

    distance_meters = summary["distance"]
    duration_seconds = summary["duration"]
    distance_miles = round(distance_meters / 1609.344, 2)

    result = {
        "distance_miles": distance_miles,
        "duration_seconds": round(duration_seconds),
        "geometry": geometry,
    }

    with _route_cache_lock:
        _DIRECTIONS_CACHE[cache_key] = result

    return result


def compute_cumulative_polyline_distances(
    coordinates: List[List[float]],
    total_distance_miles: float
) -> List[float]:
    """
    Calculate the cumulative driving distance in miles from the origin for each
    vertex along the route polyline. Calibrates to total_distance_miles.
    """
    if not coordinates:
        return []
    if len(coordinates) == 1:
        return [0.0]

    cum_dist = [0.0]
    running_total = 0.0

    r_miles = 3958.8  # Earth radius
    for i in range(1, len(coordinates)):
        lon1, lat1 = coordinates[i - 1]
        lon2, lat2 = coordinates[i]

        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlam = math.radians(lon2 - lon1)

        a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2.0) ** 2
        c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
        segment_miles = r_miles * c

        running_total += segment_miles
        cum_dist.append(running_total)

    # Scale proportionally to match the exact road distance from routing engine
    if running_total > 0:
        scale = total_distance_miles / running_total
        return [round(d * scale, 3) for d in cum_dist]
    return [0.0] * len(coordinates)
