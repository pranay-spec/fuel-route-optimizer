def calculate_fuel_cost(distance_miles, price_per_gallon, mpg=10):
    """
    Estimate fuel consumption and fuel cost.

    distance_miles: total driving distance
    price_per_gallon: fuel price in dollars
    mpg: vehicle fuel efficiency
    """
    if distance_miles < 0:
        raise ValueError("Distance cannot be negative.")

    if price_per_gallon <= 0:
        raise ValueError("Fuel price must be greater than zero.")

    if mpg <= 0:
        raise ValueError("MPG must be greater than zero.")

    gallons_needed = distance_miles / mpg
    total_cost = gallons_needed * price_per_gallon

    return {
        "distance_miles": round(distance_miles, 2),
        "mpg": mpg,
        "gallons_needed": round(gallons_needed, 2),
        "price_per_gallon": round(price_per_gallon, 3),
        "estimated_fuel_cost": round(total_cost, 2),
    }
