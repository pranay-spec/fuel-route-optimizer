import math
from typing import Any, Dict, List, Optional, Tuple

from optimizer.services.coordinates import (
    enrich_all_stations,
    haversine_distance,
)
from optimizer.services_data import load_fuel_stations


class InfeasibleRouteError(Exception):
    """Raised when a route cannot be completed within the vehicle's 500-mile range constraint."""
    pass


def project_point_to_segment(
    p_lon: float, p_lat: float,
    a_lon: float, a_lat: float,
    b_lon: float, b_lat: float
) -> Tuple[float, float]:
    """
    Project point P onto segment AB.
    Returns (t, distance_in_miles), where t in [0, 1] is the parameter along AB.
    """
    lat_mid = math.radians((a_lat + b_lat) / 2.0)
    dx = (b_lon - a_lon) * math.cos(lat_mid)
    dy = b_lat - a_lat

    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq == 0:
        return 0.0, haversine_distance(p_lat, p_lon, a_lat, a_lon)

    wx = (p_lon - a_lon) * math.cos(lat_mid)
    wy = p_lat - a_lat

    t = max(0.0, min(1.0, (wx * dx + wy * dy) / seg_len_sq))

    q_lon = a_lon + t * (b_lon - a_lon)
    q_lat = a_lat + t * (b_lat - a_lat)
    dist = haversine_distance(p_lat, p_lon, q_lat, q_lon)
    return t, dist


def find_candidate_stations_along_route(
    route_coordinates: List[List[float]],
    cumulative_distances: List[float],
    total_distance_miles: float,
    max_detour_miles: float = 15.0,
    stations_dataset: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """
    Project stations onto the route polyline to find candidates within max_detour_miles.
    Computes for each candidate:
    - 'route_mile': position along route (miles from origin)
    - 'detour_miles': perpendicular distance from station to highway
    """
    if not route_coordinates or len(route_coordinates) < 2:
        return []

    # 1. Bounding box filter for quick rejection
    lons = [pt[0] for pt in route_coordinates]
    lats = [pt[1] for pt in route_coordinates]

    deg_buffer = (max_detour_miles / 69.0) + 0.15
    min_lon, max_lon = min(lons) - deg_buffer, max(lons) + deg_buffer
    min_lat, max_lat = min(lats) - deg_buffer, max(lats) + deg_buffer

    # Load and enrich stations
    stations = stations_dataset if stations_dataset is not None else load_fuel_stations()
    enriched_stations = enrich_all_stations(stations)

    # Filter by bounding box and status
    bbox_candidates = [
        s for s in enriched_stations
        if s.get("longitude") is not None
        and s.get("latitude") is not None
        and s.get("status") in ("verified", "approximate")
        and min_lon <= s["longitude"] <= max_lon
        and min_lat <= s["latitude"] <= max_lat
    ]

    candidates_along_route = []

    # Sample polyline if very dense to keep segment projection fast
    step = max(1, len(route_coordinates) // 1500)
    sampled_indices = list(range(0, len(route_coordinates), step))
    if (len(route_coordinates) - 1) not in sampled_indices:
        sampled_indices.append(len(route_coordinates) - 1)

    sampled_coords = [route_coordinates[i] for i in sampled_indices]
    sampled_cum_dist = [cumulative_distances[i] for i in sampled_indices]

    num_segments = len(sampled_coords) - 1

    for station in bbox_candidates:
        st_lat = station["latitude"]
        st_lon = station["longitude"]

        best_dist = float("inf")
        best_route_mile = 0.0

        for seg_idx in range(num_segments):
            a_lon, a_lat = sampled_coords[seg_idx]
            b_lon, b_lat = sampled_coords[seg_idx + 1]

            # Fast bounding box check on segment
            seg_min_lat = min(a_lat, b_lat) - deg_buffer
            seg_max_lat = max(a_lat, b_lat) + deg_buffer
            seg_min_lon = min(a_lon, b_lon) - deg_buffer
            seg_max_lon = max(a_lon, b_lon) + deg_buffer

            if not (seg_min_lat <= st_lat <= seg_max_lat and seg_min_lon <= st_lon <= seg_max_lon):
                continue

            t, d = project_point_to_segment(st_lon, st_lat, a_lon, a_lat, b_lon, b_lat)
            if d < best_dist:
                best_dist = d
                a_mile = sampled_cum_dist[seg_idx]
                b_mile = sampled_cum_dist[seg_idx + 1]
                best_route_mile = a_mile + t * (b_mile - a_mile)

        if best_dist <= max_detour_miles:
            # Clamp to route bounds
            route_mile = max(0.0, min(best_route_mile, total_distance_miles))

            station_entry = {
                **station,
                "route_mile": round(route_mile, 2),
                "detour_miles": round(best_dist, 2),
            }
            candidates_along_route.append(station_entry)

    # Sort candidates by route mile marker
    candidates_along_route.sort(key=lambda s: (s["route_mile"], s["detour_miles"], s["Retail Price"]))
    return candidates_along_route


def optimize_fuel_stops(
    route_distance_miles: float,
    route_coordinates: List[List[float]],
    cumulative_distances: List[float],
    mpg: float = 10.0,
    max_range_miles: float = 500.0,
    max_detour_miles: float = 15.0,
    stations_dataset: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Find the globally cost-optimal sequence of fuel stops respecting the vehicle's
    maximum range constraint using a Directed Acyclic Graph (DAG) shortest path formulation.

    Accounting Model:
    - Vehicle starts at origin with a full tank (500 miles range).
    - If total distance <= 500 miles, no fuel stops are required.
    - If total distance > 500 miles, stops are selected such that no driving segment
      exceeds max_range_miles (500 mi).
    - At each stop, the vehicle refills fuel consumed since the previous fill (or start).
    - Destination is reachable from the final stop within remaining range.
    """
    # Case 1: Short journey <= 500 miles
    if route_distance_miles <= max_range_miles:
        gallons_consumed = round(route_distance_miles / mpg, 2)
        return {
            "fuel_summary": {
                "mpg": mpg,
                "max_range_miles": max_range_miles,
                "route_distance_miles": route_distance_miles,
                "total_detour_miles": 0.0,
                "total_driven_miles": route_distance_miles,
                "estimated_gallons_consumed": gallons_consumed,
                "total_gallons_purchased": 0.0,
                "estimated_fuel_cost": 0.0,
                "stops_count": 0,
                "accounting_note": "Route is within the 500-mile range. No refueling stops required.",
            },
            "fuel_stops": [],
        }

    # Case 2: Journey > 500 miles requiring refueling stops
    candidate_stations = find_candidate_stations_along_route(
        route_coordinates=route_coordinates,
        cumulative_distances=cumulative_distances,
        total_distance_miles=route_distance_miles,
        max_detour_miles=max_detour_miles,
        stations_dataset=stations_dataset,
    )

    pruned_candidates: List[Dict[str, Any]] = []
    for st in candidate_stations:
        # If station is practically at the origin or destination, skip
        if st["route_mile"] < 5.0 or st["route_mile"] > (route_distance_miles - 5.0):
            continue
        pruned_candidates.append(st)

    nodes: List[Dict[str, Any]] = [
        {"node_id": 0, "is_start": True, "route_mile": 0.0, "detour_miles": 0.0, "Retail Price": 0.0}
    ]
    for idx, st in enumerate(pruned_candidates, start=1):
        nodes.append({**st, "node_id": idx, "is_start": False, "is_dest": False})

    dest_id = len(nodes)
    nodes.append({
        "node_id": dest_id,
        "is_dest": True,
        "route_mile": route_distance_miles,
        "detour_miles": 0.0,
        "Retail Price": 0.0,
    })

    num_nodes = len(nodes)
    dp = [float("inf")] * num_nodes
    predecessor = [-1] * num_nodes
    segment_distance = [0.0] * num_nodes

    dp[0] = 0.0

    # Solve DAG shortest path via Dynamic Programming
    for u in range(num_nodes):
        if math.isinf(dp[u]):
            continue

        u_mile = nodes[u]["route_mile"]

        for v in range(u + 1, num_nodes):
            v_mile = nodes[v]["route_mile"]
            if v_mile < u_mile:
                continue

            # Round trip detour for station v (detour off highway and back)
            detour_v = nodes[v]["detour_miles"] * 2.0
            leg_distance = (v_mile - u_mile) + detour_v

            # Range constraint check: can the vehicle reach v on one full tank?
            if leg_distance > max_range_miles:
                if (v_mile - u_mile) > max_range_miles:
                    break
                continue

            # Edge cost calculation:
            if nodes[v].get("is_dest", False):
                # Destination has no fuel purchase
                edge_cost = 0.0
            else:
                # At station v, refill fuel consumed on the leg from u to v
                gallons = leg_distance / mpg
                purchase = gallons * nodes[v]["Retail Price"]
                # Tie-breakers: slight penalty for detour and stop count to favor direct/fewer stops
                tie_breaker = (0.01 * detour_v) + 0.1
                edge_cost = purchase + tie_breaker

            total_candidate_cost = dp[u] + edge_cost

            if total_candidate_cost < dp[v]:
                dp[v] = total_candidate_cost
                predecessor[v] = u
                segment_distance[v] = leg_distance

    # Check feasibility
    if math.isinf(dp[dest_id]):
        raise InfeasibleRouteError(
            f"No feasible fueling plan found for this {route_distance_miles:.1f}-mile route. "
            f"Distance between available fuel stations along the corridor exceeds the vehicle's "
            f"{max_range_miles:.0f}-mile maximum range."
        )

    # Reconstruct optimal path
    path = []
    curr = dest_id
    while curr != -1:
        path.append(curr)
        curr = predecessor[curr]
    path.reverse()

    # Build fuel stops output
    selected_stops = []
    total_detour = 0.0
    total_gallons_purchased = 0.0
    total_purchase_cost = 0.0

    stop_index = 1
    for node_idx in path:
        if node_idx == 0 or node_idx == dest_id:
            continue

        node_data = nodes[node_idx]
        leg_dist = segment_distance[node_idx]
        price = node_data["Retail Price"]

        gallons = round(leg_dist / mpg, 2)
        cost = round(gallons * price, 2)

        detour_round_trip = round(node_data["detour_miles"] * 2.0, 2)
        total_detour += detour_round_trip
        total_gallons_purchased += gallons
        total_purchase_cost += cost

        selected_stops.append({
            "stop_number": stop_index,
            "opis_id": node_data.get("OPIS Truckstop ID"),
            "truckstop_name": node_data.get("Truckstop Name"),
            "address": node_data.get("Address"),
            "city": node_data.get("City"),
            "state": node_data.get("State"),
            "price_per_gallon": round(price, 3),
            "route_mile": round(node_data["route_mile"], 2),
            "detour_miles": round(node_data["detour_miles"], 2),
            "distance_from_last_stop": round(leg_dist, 2),
            "gallons_purchased": gallons,
            "purchase_cost": cost,
            "coordinates": [node_data.get("longitude"), node_data.get("latitude")],
            "coordinate_confidence": node_data.get("confidence", "approximate"),
            "coordinate_source": node_data.get("source", "none"),
            "is_exact": node_data.get("is_exact", False),
        })
        stop_index += 1

    total_driven_miles = round(route_distance_miles + total_detour, 2)
    estimated_gallons_consumed = round(total_driven_miles / mpg, 2)

    return {
        "fuel_summary": {
            "mpg": mpg,
            "max_range_miles": max_range_miles,
            "route_distance_miles": route_distance_miles,
            "total_detour_miles": round(total_detour, 2),
            "total_driven_miles": total_driven_miles,
            "estimated_gallons_consumed": estimated_gallons_consumed,
            "total_gallons_purchased": round(total_gallons_purchased, 2),
            "estimated_fuel_cost": round(total_purchase_cost, 2),
            "stops_count": len(selected_stops),
            "accounting_note": (
                "Vehicle begins with a full tank. At each stop, the vehicle refuels fuel consumed "
                "since the previous fill. Destination is reached within remaining tank range."
            ),
        },
        "fuel_stops": selected_stops,
    }
