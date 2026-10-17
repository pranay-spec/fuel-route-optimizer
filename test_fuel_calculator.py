
from optimizer.services.fuel_calculator import calculate_fuel_cost

if __name__ == "__main__":
    result = calculate_fuel_cost(
        distance_miles=279.92,
        price_per_gallon=3.50,
        mpg=10,
    )
    print(result)

