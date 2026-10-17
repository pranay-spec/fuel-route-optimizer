
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status
from requests.exceptions import RequestException

from .serializers import RouteRequestSerializer
from .services.routing import (
    geocode_location,
    get_driving_route,
)


@api_view(["GET"])
def home(request):
    return Response({
        "message": "Fuel Route Optimizer API is running!",
        "status": "success"
    })


@api_view(["POST"])
def route_optimize(request):
    # Step 1: Validate the incoming JSON.
    serializer = RouteRequestSerializer(data=request.data)

    if not serializer.is_valid():
        return Response(
            {
                "status": "error",
                "errors": serializer.errors
            },
            status=status.HTTP_400_BAD_REQUEST
        )

    start = serializer.validated_data["start"]
    destination = serializer.validated_data["destination"]

    try:
        # Step 2: Convert place names into coordinates.
        start_coordinates = geocode_location(start)
        destination_coordinates = geocode_location(destination)

        # Step 3: Request the driving route.
        route = get_driving_route(
            start_coordinates,
            destination_coordinates
        )

        # Step 4: Calculate estimated fuel consumption.
        distance_miles = route["distance_miles"]
        miles_per_gallon = 10

        gallons_consumed = distance_miles / miles_per_gallon

        # Step 5: Return route data to the client.
        return Response({
            "status": "success",
            "route": {
                "start": start,
                "destination": destination,
                "distance_miles": distance_miles,
                "duration_seconds": route["duration_seconds"],
                "geometry": route["geometry"]
            },
            "fuel_summary": {
                "miles_per_gallon": miles_per_gallon,
                "estimated_gallons_consumed": round(
                    gallons_consumed, 2
                )
            }
        }, status=status.HTTP_200_OK)

    except ValueError as exc:
        return Response({
            "status": "error",
            "message": str(exc)
        }, status=status.HTTP_400_BAD_REQUEST)

    except RequestException:
        return Response({
            "status": "error",
            "message": "The routing service request failed."
        }, status=status.HTTP_502_BAD_GATEWAY)

    except RuntimeError:
        return Response({
            "status": "error",
            "message": "The routing service is not configured correctly."
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
