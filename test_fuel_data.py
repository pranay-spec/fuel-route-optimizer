from optimizer.services import load_fuel_stations


stations = load_fuel_stations()

print("Total cleaned US stations:", len(stations))
print("First station:", stations[0])

prices = [station["Retail Price"] for station in stations]

print("Minimum price:", min(prices))
print("Maximum price:", max(prices))
print("Average price:", sum(prices) / len(prices))
