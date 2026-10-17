import logging
from requests.exceptions import RequestException
from django.shortcuts import render
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .serializers import RouteRequestSerializer
from .services.optimizer import InfeasibleRouteError, optimize_fuel_stops
from .services.routing import (
    compute_cumulative_polyline_distances,
    geocode_location,
    get_driving_route,
)

logger = logging.getLogger(__name__)


def map_view(request):
    """Serve the interactive Leaflet map dashboard."""
    return render(request, "map.html")


@api_view(["GET"])
def home(request):
    """API health check and directory of available endpoints."""
    return Response(
        {
            "status": "success",
            "message": "Fuel Route Optimizer API is running!",
            "version": "v1",
            "endpoints": {
                "map_ui": "/",
                "health": "/api/v1/",
                "route_optimize": "/api/v1/route-optimize/",
            },
        },
        status=status.HTTP_200_OK,
    )


@api_view(["POST"])
def route_optimize(request):
    """
    Accepts starting location and destination within the United States.
    Returns:
    - Driving route distance, duration, and GeoJSON geometry.
    - Cost-effective fuel stops satisfying the vehicle's 500-mile range at 10 MPG.
    - Complete fuel consumption and purchase cost breakdown.
    """
    # Step 1: Validate input JSON payload
    serializer = RouteRequestSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(
            {
                "status": "error",
                "message": "Validation failed for route request parameters.",
                "errors": serializer.errors,
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    start = serializer.validated_data["start"]
    destination = serializer.validated_data["destination"]
    max_detour = serializer.validated_data.get("max_detour_miles", 15.0)

    vehicle = serializer.validated_data.get("vehicle") or {}
    mpg = float(vehicle.get("miles_per_gallon", 10.0))
    max_range = float(vehicle.get("max_range_miles", 500.0))

    try:
        # Step 2: Geocode origin and destination within the United States
        start_coords = geocode_location(start)
        dest_coords = geocode_location(destination)

        # Step 3: Compute driving route geometry and distance via OpenRouteService
        route = get_driving_route(start_coords, dest_coords)

        distance_miles = route["distance_miles"]
        duration_seconds = route["duration_seconds"]
        geometry = route["geometry"]

        # Step 4: Calculate cumulative distances along route polyline
        coords = geometry.get("coordinates", [])
        cumulative_distances = compute_cumulative_polyline_distances(coords, distance_miles)

        # Step 5: Optimize fuel stops along the route respecting 500-mile vehicle range
        optimization_result = optimize_fuel_stops(
            route_distance_miles=distance_miles,
            route_coordinates=coords,
            cumulative_distances=cumulative_distances,
            mpg=mpg,
            max_range_miles=max_range,
            max_detour_miles=max_detour,
        )

        hours = int(duration_seconds // 3600)
        minutes = int((duration_seconds % 3600) // 60)
        duration_formatted = f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"

        # Step 6: Return complete structured result
        return Response(
            {
                "status": "success",
                "route": {
                    "start": start,
                    "destination": destination,
                    "distance_miles": distance_miles,
                    "duration_seconds": duration_seconds,
                    "duration_formatted": duration_formatted,
                    "geometry": geometry,
                    "start_point": {
                        "name": start,
                        "latitude": start_coords[1],
                        "longitude": start_coords[0],
                    },
                    "destination_point": {
                        "name": destination,
                        "latitude": dest_coords[1],
                        "longitude": dest_coords[0],
                    },
                },
                "fuel_summary": optimization_result["fuel_summary"],
                "fuel_stops": optimization_result["fuel_stops"],
            },
            status=status.HTTP_200_OK,
        )

    except ValueError as exc:
        return Response(
            {
                "status": "error",
                "message": str(exc),
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    except InfeasibleRouteError as exc:
        return Response(
            {
                "status": "infeasible",
                "message": str(exc),
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    except RequestException as exc:
        logger.error(f"Upstream routing service request failure: {exc}")
        msg = str(exc) if "Routing service error:" in str(exc) else "The upstream routing service request failed or timed out. Please try again shortly."
        return Response(
            {
                "status": "error",
                "message": msg,
            },
            status=status.HTTP_502_BAD_GATEWAY,
        )

    except RuntimeError as exc:
        logger.error(f"Routing configuration error: {exc}")
        return Response(
            {
                "status": "error",
                "message": "The routing service is not configured correctly on the server.",
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
