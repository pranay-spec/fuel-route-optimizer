"""
Optional live integration test script for manual verification of the Fuel Route Optimizer.
Requires a valid ORS_API_KEY in .env.
"""
import json
import os
import django

if __name__ == "__main__":
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    django.setup()

    from django.test import Client

    client = Client()
    print("=" * 60)
    print("FUEL ROUTE OPTIMIZER - LIVE INTEGRATION TEST")
    print("=" * 60)

    # 1. API Health Check
    print("\n1. Testing GET /api/v1/ (Health check):")
    res_health = client.get("/api/v1/")
    print(f"Status Code: {res_health.status_code}")
    print(f"Response: {json.dumps(res_health.json(), indent=2)}")

    # 2. Short Route: Los Angeles to Las Vegas (~280 miles <= 500)
    print("\n2. Testing Short Route (<= 500 miles, expecting 0 stops):")
    print("Origin: Los Angeles, CA, USA -> Destination: Las Vegas, NV, USA")
    res_short = client.post(
        "/api/v1/route-optimize/",
        data=json.dumps({"start": "Los Angeles, CA, USA", "destination": "Las Vegas, NV, USA"}),
        content_type="application/json",
    )
    print(f"Status Code: {res_short.status_code}")
    data_short = res_short.json()
    print(f"Status: {data_short.get('status')}")
    print(f"Route Distance: {data_short.get('route', {}).get('distance_miles')} miles")
    print(f"Estimated Gallons Consumed: {data_short.get('fuel_summary', {}).get('estimated_gallons_consumed')} gal")
    print(f"Stops Count: {len(data_short.get('fuel_stops', []))}")
    print(f"Fuel Stops: {data_short.get('fuel_stops')}")

    # 3. Long Route: Los Angeles to Dallas (~1,440 miles > 500)
    print("\n3. Testing Long Cross-Country Route (> 500 miles, expecting multiple stops):")
    print("Origin: Los Angeles, CA, USA -> Destination: Dallas, TX, USA")
    res_long = client.post(
        "/api/v1/route-optimize/",
        data=json.dumps({"start": "Los Angeles, CA, USA", "destination": "Dallas, TX, USA"}),
        content_type="application/json",
    )
    print(f"Status Code: {res_long.status_code}")
    data_long = res_long.json()
    print(f"Status: {data_long.get('status')}")
    print(f"Route Distance: {data_long.get('route', {}).get('distance_miles')} miles")
    fuel_summary = data_long.get("fuel_summary", {})
    print(f"Total Driven Distance: {fuel_summary.get('total_driven_miles')} miles")
    print(f"Total Gallons Consumed: {fuel_summary.get('estimated_gallons_consumed')} gal")
    print(f"Total Gallons Purchased: {fuel_summary.get('total_gallons_purchased')} gal")
    print(f"Total Fuel Cost: ${fuel_summary.get('estimated_fuel_cost')}")
    stops = data_long.get("fuel_stops", [])
    print(f"Stops Count: {len(stops)}")
    print("\nSelected Fuel Stops:")
    for stop in stops:
        print(
            f"  - Stop #{stop['stop_number']}: {stop['truckstop_name']} ({stop['city']}, {stop['state']}) "
            f"@ mile {stop['route_mile']:.1f} | Detour: {stop['detour_miles']} mi | "
            f"Price: ${stop['price_per_gallon']:.3f}/gal | Purchased: {stop['gallons_purchased']} gal | "
            f"Cost: ${stop['purchase_cost']:.2f}"
        )
    print("=" * 60)
    print("Live integration test completed successfully.")
