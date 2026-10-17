from optimizer.services import load_fuel_stations

stations = load_fuel_stations()

print("Available columns:")
print(list(stations[0].keys()))

print("\nSample addresses:")
for station in stations[:20]:
    print(
        station["Address"],
        "|",
        station["City"],
        "|",
        station["State"]
    )

print("\nStations with ZIP-like addresses:")
zip_count = sum(
    any(char.isdigit() for char in station["Address"])
    for station in stations
)
print(zip_count)
