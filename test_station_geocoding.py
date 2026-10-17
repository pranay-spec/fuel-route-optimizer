
import os
import django

if __name__ == "__main__":
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    django.setup()

    from optimizer.services import load_fuel_stations
    from optimizer.services.routing import geocode_location

    stations = load_fuel_stations()

    for station in stations[:5]:
        location = (
            f'{station["Address"]}, '
            f'{station["City"]}, '
            f'{station["State"]}, USA'
        )

        print("\nStation:", station["Truckstop Name"])
        print("Searching:", location)

        try:
            coordinates = geocode_location(location)
            print("Coordinates:", coordinates)
        except Exception as error:
            print("Geocoding failed:", error)

