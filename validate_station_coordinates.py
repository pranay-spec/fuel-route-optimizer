
import os
import django

if __name__ == "__main__":
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    django.setup()

    from optimizer.services import load_fuel_stations
    from optimizer.services.routing import geocode_location

    stations = load_fuel_stations()

    for station in stations[:5]:
        # Test city and state without the highway address.
        location = f'{station["City"]}, {station["State"]}, USA'

        print("\nStation:", station["Truckstop Name"])
        print("Expected city/state:", location)

        try:
            coordinates = geocode_location(location)
            print("City coordinates:", coordinates)
        except Exception as error:
            print("Geocoding failed:", error)

