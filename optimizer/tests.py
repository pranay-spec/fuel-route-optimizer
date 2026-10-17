import json
from unittest.mock import MagicMock, patch
import pandas as pd
from django.conf import settings
from django.test import Client, TestCase
from requests.exceptions import RequestException, Timeout

from optimizer.serializers import RouteRequestSerializer
from optimizer.services.coordinates import (
    haversine_distance,
    validate_coordinates,
)
from optimizer.services.fuel_calculator import calculate_fuel_cost
from optimizer.services.optimizer import (
    InfeasibleRouteError,
    find_candidate_stations_along_route,
    optimize_fuel_stops,
)
from optimizer.services.routing import (
    clear_routing_caches,
    compute_cumulative_polyline_distances,
    geocode_location,
    get_driving_route,
)
from optimizer.services_data import (
    clean_fuel_dataframe,
    clear_fuel_stations_cache,
    load_fuel_stations,
)


class HealthEndpointTests(TestCase):
    """Test 1: Health endpoint returns success."""

    def setUp(self):
        self.client = Client()

    def test_health_check_success(self):
        response = self.client.get("/api/v1/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data.get("status"), "success")
        self.assertIn("message", data)
        self.assertIn("version", data)
        self.assertIn("endpoints", data)


class RouteValidationTests(TestCase):
    """Tests 3, 4, 5: Input validation tests."""

    def setUp(self):
        self.client = Client()

    def test_missing_origin(self):
        response = self.client.post(
            "/api/v1/route-optimize/",
            data=json.dumps({"destination": "Las Vegas, NV, USA"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertEqual(data.get("status"), "error")
        self.assertIn("start", data.get("errors", {}))

    def test_missing_destination(self):
        response = self.client.post(
            "/api/v1/route-optimize/",
            data=json.dumps({"start": "Los Angeles, CA, USA"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertEqual(data.get("status"), "error")
        self.assertIn("destination", data.get("errors", {}))

    def test_empty_strings(self):
        response = self.client.post(
            "/api/v1/route-optimize/",
            data=json.dumps({"start": "   ", "destination": "   "}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertEqual(data.get("status"), "error")

    def test_identical_origin_and_destination(self):
        response = self.client.post(
            "/api/v1/route-optimize/",
            data=json.dumps({"start": "Los Angeles, CA", "destination": "Los Angeles, CA"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn("destination", data.get("errors", {}))

    def test_malformed_json_body(self):
        response = self.client.post(
            "/api/v1/route-optimize/",
            data="invalid-raw-json",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)


class RoutingAndUpstreamTests(TestCase):
    """Tests 6, 7: Geocoding and upstream routing errors."""

    def setUp(self):
        self.client = Client()
        clear_routing_caches()

    @patch("optimizer.views.geocode_location")
    def test_geocoding_failure_returns_400(self, mock_geocode):
        mock_geocode.side_effect = ValueError("Could not find a valid US location for: 'Nowhere'")
        response = self.client.post(
            "/api/v1/route-optimize/",
            data=json.dumps({"start": "Nowhere", "destination": "Las Vegas, NV, USA"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertEqual(data.get("status"), "error")
        self.assertIn("Nowhere", data.get("message", ""))

    @patch("optimizer.views.geocode_location")
    @patch("optimizer.views.get_driving_route")
    def test_routing_service_timeout_returns_502(self, mock_route, mock_geocode):
        mock_geocode.return_value = [-118.2437, 34.0522]
        mock_route.side_effect = Timeout("Routing service timed out after 30 seconds.")
        response = self.client.post(
            "/api/v1/route-optimize/",
            data=json.dumps({"start": "Los Angeles, CA, USA", "destination": "Las Vegas, NV, USA"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 502)
        data = response.json()
        self.assertEqual(data.get("status"), "error")

    @patch("optimizer.views.geocode_location")
    @patch("optimizer.views.get_driving_route")
    def test_routing_service_upstream_error_returns_502(self, mock_route, mock_geocode):
        mock_geocode.return_value = [-118.2437, 34.0522]
        mock_route.side_effect = RequestException("503 Server Error")
        response = self.client.post(
            "/api/v1/route-optimize/",
            data=json.dumps({"start": "Los Angeles, CA, USA", "destination": "Las Vegas, NV, USA"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 502)
        data = response.json()
        self.assertEqual(data.get("status"), "error")

    @patch("optimizer.views.geocode_location")
    def test_routing_service_runtime_config_error_returns_500(self, mock_geocode):
        mock_geocode.side_effect = RuntimeError("ORS_API_KEY is missing from configuration / .env")
        response = self.client.post(
            "/api/v1/route-optimize/",
            data=json.dumps({"start": "Los Angeles, CA, USA", "destination": "Las Vegas, NV, USA"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 500)
        data = response.json()
        self.assertEqual(data.get("status"), "error")


class FuelDataCleaningTests(TestCase):
    """Tests 8, 9: Fuel dataset cleaning, schema validation, deduplication."""

    def setUp(self):
        clear_fuel_stations_cache()

    def test_missing_csv_columns(self):
        bad_df = pd.DataFrame({
            "OPIS Truckstop ID": [1],
            "Truckstop Name": ["Test Station"],
            # Missing Address, City, State, Retail Price
        })
        with self.assertRaises(ValueError) as ctx:
            clean_fuel_dataframe(bad_df)
        self.assertIn("missing required column", str(ctx.exception).lower())

    def test_invalid_and_negative_prices_excluded(self):
        raw_df = pd.DataFrame({
            "OPIS Truckstop ID": [1, 2, 3, 4],
            "Truckstop Name": ["S1", "S2", "S3", "S4"],
            "Address": ["A1", "A2", "A3", "A4"],
            "City": ["Austin", "Austin", "Austin", "Austin"],
            "State": ["TX", "TX", "TX", "TX"],
            "Rack ID": [10, 10, 10, 10],
            "Retail Price": [3.25, -1.50, 0.0, "INVALID"],
        })
        clean = clean_fuel_dataframe(raw_df)
        self.assertEqual(len(clean), 1)
        self.assertEqual(clean.iloc[0]["Retail Price"], 3.25)

    def test_duplicate_station_deduplication(self):
        raw_df = pd.DataFrame({
            "OPIS Truckstop ID": [1, 1, 2],
            "Truckstop Name": ["Station A", "Station A", "Station B"],
            "Address": ["100 Main St", "100 Main St", "200 Oak St"],
            "City": ["Dallas", "Dallas", "Houston"],
            "State": ["TX", "TX", "TX"],
            "Rack ID": [1, 1, 2],
            "Retail Price": [3.10, 3.10, 3.20],
        })
        clean = clean_fuel_dataframe(raw_df)
        self.assertEqual(len(clean), 2)

    def test_non_us_records_excluded(self):
        raw_df = pd.DataFrame({
            "OPIS Truckstop ID": [1, 2],
            "Truckstop Name": ["Ontario Station", "California Station"],
            "Address": ["401 Hwy", "I-5 Exit 10"],
            "City": ["Toronto", "Bakersfield"],
            "State": ["ON", "CA"],
            "Rack ID": [1, 2],
            "Retail Price": [3.50, 4.20],
        })
        clean = clean_fuel_dataframe(raw_df)
        self.assertEqual(len(clean), 1)
        self.assertEqual(clean.iloc[0]["State"], "CA")


class CoordinateValidationTests(TestCase):
    """Test 10: Station coordinate validation and confidence labeling."""

    def test_out_of_state_coordinates_rejected(self):
        # Coordinates in Florida (lat 28.5, lon -81.3) passed with State 'CA'
        is_valid, msg = validate_coordinates(28.5, -81.3, "CA")
        self.assertFalse(is_valid)
        self.assertIn("outside state CA", msg)

    def test_coordinates_far_from_city_centroid_rejected(self):
        # Big Cabin, OK centroid is approx (36.54, -95.22)
        # Elk City, OK coordinates (35.25, -99.65) are ~260 miles away
        is_valid, msg = validate_coordinates(
            lat=35.25,
            lon=-99.65,
            state="OK",
            city_centroid=(36.54, -95.22),
            max_deviation_miles=45.0,
        )
        self.assertFalse(is_valid)
        self.assertIn("threshold", msg)

    def test_valid_coordinates_accepted(self):
        # Los Angeles, CA coordinates within CA bounds and near centroid
        is_valid, msg = validate_coordinates(
            lat=34.05,
            lon=-118.25,
            state="CA",
            city_centroid=(34.05, -118.25),
        )
        self.assertTrue(is_valid)
        self.assertEqual(msg, "Valid")


class FuelOptimizationAlgorithmTests(TestCase):
    """Tests 11, 12, 13, 14, 15, 16, 17, 18: Fuel optimization scenarios."""

    def setUp(self):
        self.coords_line = [[-120.0 + i * (10.0 / 14), 34.0] for i in range(15)]
        self.cum_dist_line = [i * 100.0 for i in range(15)]  # 0 to 1400 miles

    def test_journey_shorter_than_vehicle_range(self):
        """Test 11: Journey <= 500 miles requires 0 fuel stops."""
        result = optimize_fuel_stops(
            route_distance_miles=280.0,
            route_coordinates=[[-118.25, 34.05], [-115.28, 36.19]],
            cumulative_distances=[0.0, 280.0],
            mpg=10.0,
            max_range_miles=500.0,
        )
        summary = result["fuel_summary"]
        self.assertEqual(summary["stops_count"], 0)
        self.assertEqual(summary["estimated_gallons_consumed"], 28.0)
        self.assertEqual(summary["total_gallons_purchased"], 0.0)
        self.assertEqual(summary["estimated_fuel_cost"], 0.0)
        self.assertEqual(result["fuel_stops"], [])

    def test_journey_requiring_one_fuel_stop(self):
        """Test 12: Journey ~800 miles requires exactly 1 fuel stop."""
        fake_stations = [
            {
                "OPIS Truckstop ID": 101,
                "Truckstop Name": "Midway Station",
                "Address": "Mile 400",
                "City": "Midway",
                "State": "CA",
                "latitude": 34.0,
                "longitude": self.coords_line[4][0],  # mile 400
                "status": "approximate",
                "Retail Price": 3.20,
                "confidence": "city_centroid",
                "source": "test",
            }
        ]
        result = optimize_fuel_stops(
            route_distance_miles=800.0,
            route_coordinates=self.coords_line[:9],
            cumulative_distances=self.cum_dist_line[:9],
            mpg=10.0,
            max_range_miles=500.0,
            stations_dataset=fake_stations,
        )
        self.assertEqual(result["fuel_summary"]["stops_count"], 1)
        stops = result["fuel_stops"]
        self.assertEqual(len(stops), 1)
        self.assertEqual(stops[0]["opis_id"], 101)
        self.assertEqual(stops[0]["gallons_purchased"], 40.0)
        self.assertEqual(stops[0]["purchase_cost"], round(40.0 * 3.20, 2))

    def test_journey_requiring_multiple_fuel_stops(self):
        """Test 13: Journey ~1300 miles requires multiple fuel stops."""
        fake_stations = [
            {
                "OPIS Truckstop ID": 201,
                "Truckstop Name": "Stop 1",
                "Address": "A1",
                "City": "C1",
                "State": "CA",
                "latitude": 34.0,
                "longitude": self.coords_line[4][0],  # mile 400
                "status": "approximate",
                "Retail Price": 3.00,
                "confidence": "city_centroid",
                "source": "test",
            },
            {
                "OPIS Truckstop ID": 202,
                "Truckstop Name": "Stop 2",
                "Address": "A2",
                "City": "C2",
                "State": "CA",
                "latitude": 34.0,
                "longitude": self.coords_line[8][0],  # mile 800
                "status": "approximate",
                "Retail Price": 3.10,
                "confidence": "city_centroid",
                "source": "test",
            },
        ]
        result = optimize_fuel_stops(
            route_distance_miles=1200.0,
            route_coordinates=self.coords_line[:13],
            cumulative_distances=self.cum_dist_line[:13],
            mpg=10.0,
            max_range_miles=500.0,
            stations_dataset=fake_stations,
        )
        stops = result["fuel_stops"]
        self.assertEqual(len(stops), 2)
        self.assertEqual(stops[0]["opis_id"], 201)
        self.assertEqual(stops[1]["opis_id"], 202)
        # Leg 1: 0 to 400 = 400 mi -> 40 gal @ 3.00 = $120.00
        # Leg 2: 400 to 800 = 400 mi -> 40 gal @ 3.10 = $124.00
        # Leg 3: 800 to 1200 = 400 mi <= 500 (destination reachable!)
        self.assertEqual(result["fuel_summary"]["estimated_fuel_cost"], 244.0)

    def test_no_reachable_station_within_range_raises_infeasible(self):
        """Test 14: Gap > 500 miles raises InfeasibleRouteError."""
        # 1000 miles with zero stations
        with self.assertRaises(InfeasibleRouteError):
            optimize_fuel_stops(
                route_distance_miles=1000.0,
                route_coordinates=self.coords_line[:11],
                cumulative_distances=self.cum_dist_line[:11],
                mpg=10.0,
                max_range_miles=500.0,
                stations_dataset=[],
            )

    def test_destination_reachable_without_unnecessary_stop(self):
        """Test 16: When destination is reachable, no unnecessary stop is added."""
        fake_stations = [
            {
                "OPIS Truckstop ID": 301,
                "Truckstop Name": "Necessary Stop",
                "Address": "M400",
                "City": "City1",
                "State": "CA",
                "latitude": 34.0,
                "longitude": self.coords_line[4][0],  # mile 400
                "status": "approximate",
                "Retail Price": 3.00,
                "confidence": "city_centroid",
                "source": "test",
            },
            {
                "OPIS Truckstop ID": 302,
                "Truckstop Name": "Unnecessary Stop",
                "Address": "M600",
                "City": "City2",
                "State": "CA",
                "latitude": 34.0,
                "longitude": self.coords_line[6][0],  # mile 600
                "status": "approximate",
                "Retail Price": 3.50,
                "confidence": "city_centroid",
                "source": "test",
            },
        ]
        # Route is 750 miles. After mile 400 stop, remaining distance is 350 <= 500.
        result = optimize_fuel_stops(
            route_distance_miles=750.0,
            route_coordinates=self.coords_line[:8],
            cumulative_distances=self.cum_dist_line[:8],
            mpg=10.0,
            max_range_miles=500.0,
            stations_dataset=fake_stations,
        )
        self.assertEqual(len(result["fuel_stops"]), 1)
        self.assertEqual(result["fuel_stops"][0]["opis_id"], 301)

    def test_fuel_consumption_and_purchase_arithmetic(self):
        """Test 17: Accurate fuel consumption and purchase cost math."""
        # Standalone helper
        res = calculate_fuel_cost(distance_miles=500.0, price_per_gallon=3.50, mpg=10.0)
        self.assertEqual(res["gallons_needed"], 50.0)
        self.assertEqual(res["estimated_fuel_cost"], 175.0)

    def test_excessive_detour_station_excluded(self):
        """Test 18: Station with detour exceeding max_detour_miles is excluded."""
        # 30 miles off-route station (latitude offset ~ 0.44 degrees)
        fake_stations = [
            {
                "OPIS Truckstop ID": 401,
                "Truckstop Name": "Far Away Station",
                "Address": "Off-road",
                "City": "Far",
                "State": "CA",
                "latitude": 34.45,  # ~31 miles north
                "longitude": self.coords_line[4][0],
                "status": "approximate",
                "Retail Price": 2.00,
                "confidence": "city_centroid",
                "source": "test",
            }
        ]
        candidates = find_candidate_stations_along_route(
            route_coordinates=self.coords_line[:8],
            cumulative_distances=self.cum_dist_line[:8],
            total_distance_miles=700.0,
            max_detour_miles=15.0,
            stations_dataset=fake_stations,
        )
        self.assertEqual(len(candidates), 0)


class EndToEndAPIAndCachingTests(TestCase):
    """Tests 2, 19, 20: Valid API response structure, caching, single routing call."""

    def setUp(self):
        self.client = Client()
        clear_routing_caches()

    @patch("optimizer.views.geocode_location")
    @patch("optimizer.views.get_driving_route")
    def test_valid_route_request_response_structure(self, mock_route, mock_geocode):
        """Test 2: Valid route returns documented schema structure."""
        mock_geocode.side_effect = [
            [-118.25703, 34.05513],  # LA
            [-115.27910, 36.19036],  # LV
        ]
        mock_route.return_value = {
            "distance_miles": 279.92,
            "duration_seconds": 17116,
            "geometry": {
                "type": "LineString",
                "coordinates": [[-118.257, 34.055], [-115.279, 36.190]],
            },
        }

        response = self.client.post(
            "/api/v1/route-optimize/",
            data=json.dumps({"start": "Los Angeles, CA, USA", "destination": "Las Vegas, NV, USA"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data.get("status"), "success")
        self.assertIn("route", data)
        self.assertIn("fuel_summary", data)
        self.assertIn("fuel_stops", data)

        route_info = data["route"]
        self.assertEqual(route_info["distance_miles"], 279.92)
        self.assertEqual(route_info["duration_seconds"], 17116)
        self.assertEqual(route_info["geometry"]["type"], "LineString")

        summary = data["fuel_summary"]
        self.assertEqual(summary["mpg"], 10.0)
        self.assertEqual(summary["max_range_miles"], 500.0)
        self.assertEqual(summary["estimated_gallons_consumed"], 27.99)

    @patch("optimizer.services.routing.requests.get")
    def test_geocoding_cache_reuses_data(self, mock_get):
        """Test 19: Repeated requests reuse cached geocoding and routing data."""
        clear_routing_caches()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "features": [{"geometry": {"coordinates": [-118.25, 34.05]}}]
        }
        mock_get.return_value = mock_response

        # Call twice with same location
        c1 = geocode_location("Los Angeles, CA")
        c2 = geocode_location("Los Angeles, CA")

        self.assertEqual(c1, c2)
        # Should only call requests.get ONCE due to caching
        self.assertEqual(mock_get.call_count, 1)

    @patch("optimizer.views.geocode_location")
    @patch("optimizer.views.get_driving_route")
    def test_single_directions_call_per_request(self, mock_route, mock_geocode):
        """Test 20: A route request does not trigger one directions API call per station."""
        mock_geocode.side_effect = [
            [-118.25, 34.05],
            [-115.28, 36.19],
        ]
        mock_route.return_value = {
            "distance_miles": 279.92,
            "duration_seconds": 17116,
            "geometry": {
                "type": "LineString",
                "coordinates": [[-118.25, 34.05], [-115.28, 36.19]],
            },
        }

        self.client.post(
            "/api/v1/route-optimize/",
            data=json.dumps({"start": "Los Angeles, CA, USA", "destination": "Las Vegas, NV, USA"}),
            content_type="application/json",
        )
        # Exactly 1 directions call made!
        self.assertEqual(mock_route.call_count, 1)
