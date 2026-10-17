
import os
import django

# Tell Django which settings module to use.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

# Initialize Django.
django.setup()

# Import routing functions only after Django is initialized.
from optimizer.services.routing import (
    geocode_location,
    get_driving_route,
)


def main():
    start = geocode_location("Los Angeles, CA, USA")
    destination = geocode_location("Las Vegas, NV, USA")

    print("Start coordinates:", start)
    print("Destination coordinates:", destination)

    route = get_driving_route(start, destination)

    print("\nDriving distance:", route["distance_miles"], "miles")
    print("Duration:", route["duration_seconds"], "seconds")
    print("Geometry type:", route["geometry"]["type"])
    print(
        "Number of route coordinates:",
        len(route["geometry"]["coordinates"])
    )


if __name__ == "__main__":
    main()
