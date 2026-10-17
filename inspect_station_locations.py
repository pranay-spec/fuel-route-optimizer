from optimizer.services import load_fuel_stations

stations = load_fuel_stations()

print("Total US stations:", len(stations))
print("\nFirst 10 station locations:\n")

for station in stations[:10]:
    print(
        station["Truckstop Name"],
        "|",
        station["Address"],
        "|",
        station["City"],
        "|",
        station["State"]
    )

# Count distinct city/state combinations.
city_state_pairs = {
    (
        station["City"],
        station["State"]
    )
    for station in stations
}

print("\nUnique city/state combinations:", len(city_state_pairs))
