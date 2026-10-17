import requests

from django.conf import settings

BASE_URL = "https://api.heigit.org"

def geocode_location(location):
    """
    Convert a place name into longitude and latitude.
    """

    api_key = settings.ORS_API_KEY

    if not api_key:
        raise RuntimeError("ORS_API_KEY is missing from .env")

    response = requests.get(
        f"{BASE_URL}/pelias/v1/search",
        params={
            "text": location,
            "size": 1,
            "boundary.country": "USA",
        },
        headers={
            "Authorization": api_key,
        },
        timeout=15,
    )

    response.raise_for_status()
    data = response.json()

    features = data.get("features", [])

    if not features:
        raise ValueError(
            f"Could not find a US location for: {location}"
        )

    coordinates = features[0]["geometry"]["coordinates"]

    # GeoJSON uses longitude first, latitude second.
    return coordinates


def get_driving_route(start_coordinates, end_coordinates):
    """
    Retrieve a driving route between two coordinate pairs.
    """

    api_key = settings.ORS_API_KEY

    if not api_key:
        raise RuntimeError("ORS_API_KEY is missing from .env")

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
        },
        timeout=30,
    )

    response.raise_for_status()
    data = response.json()

    features = data.get("features", [])

    if not features:
        raise ValueError("The routing service returned no route.")

    feature = features[0]
    properties = feature["properties"]
    geometry = feature["geometry"]

    distance_meters = properties["summary"]["distance"]
    duration_seconds = properties["summary"]["duration"]

    return {
        "distance_miles": round(distance_meters / 1609.344, 2),
        "duration_seconds": round(duration_seconds),
        "geometry": geometry,
    }
