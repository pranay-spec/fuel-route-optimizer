# Fuel Route Optimizer API 🚛⛽

A production-grade Django REST Framework service that computes the driving route between two US locations, identifies cost-effective fuel stops along the route corridor using real fuel prices, respects the vehicle's 500-mile range constraint at 10 miles per gallon (MPG), and returns complete GeoJSON route geometry and financial summaries.

---

## 1. Overview & Business Requirements

- **Country Restriction**: United States only.
- **Vehicle Fuel Efficiency**: 10.0 Miles Per Gallon (MPG).
- **Maximum Vehicle Range**: 500 miles on a full 50-gallon tank.
- **Fuel Prices**: Real OPIS fuel prices loaded from `data/fuel-prices-for-be-assessment.csv` (no fabricated or hardcoded prices).
- **Routing & Geocoding**: Integrated with OpenRouteService (Pelias Search & Directions v2).
- **Efficiency**: Exactly **1 routing API call per route request**; station candidates are geographically filtered and evaluated along the route polyline without per-station directions calls.
- **Resilience**: Complete input validation, graceful handling of upstream service timeouts/failures, and explicit responses for infeasible routes.

---

## 2. Architecture & Directory Structure

```text
Fuel Optimizer manual/
├── config/                                 # Django Project Configuration
│   ├── settings.py                         # Settings, ORS API key, database, apps
│   ├── urls.py                             # Root router (routes /api/ to optimizer)
│   ├── wsgi.py / asgi.py                   # WSGI / ASGI entry points
│   └── __init__.py
├── optimizer/                              # Django Application
│   ├── management/
│   │   └── commands/
│   │       └── enrich_coordinates.py       # Management command to audit/populate coordinate cache
│   ├── migrations/
│   │   └── 0001_initial.py                 # FuelStationCoordinate database schema
│   ├── services/                           # Core Business Logic & Algorithms
│   │   ├── __init__.py                     # Package export
│   │   ├── coordinates.py                  # US Census Gazetteer, Haversine, State bounds validator
│   │   ├── fuel_calculator.py              # Pure fuel cost math helper
│   │   ├── optimizer.py                    # Polyline segment projection & DAG shortest-path optimizer
│   │   └── routing.py                      # OpenRouteService client, caching, polyline cumulative miles
│   ├── admin.py                            # Django Admin configuration
│   ├── apps.py                             # Optimizer AppConfig
│   ├── models.py                           # FuelStationCoordinate persistent model
│   ├── serializers.py                      # RouteRequestSerializer input validation
│   ├── services_data.py                    # Fuel CSV parser, cleaning, and in-memory caching
│   ├── tests.py                            # 27 automated unit & scenario tests
│   ├── urls.py                             # /v1/ and /v1/route-optimize/ endpoints
│   └── views.py                            # API controllers with error handling
├── data/
│   ├── fuel-prices-for-be-assessment.csv   # Raw fuel stations dataset (8,151 rows)
│   ├── station_coordinates_cache.json      # Offline persistent coordinate cache (7,086+ stations)
│   └── us_cities_census.json               # Official US Census Bureau gazetteer dataset (64,000+ places)
├── .env.example                            # Example environment variable template
├── .gitignore                              # Git exclusion rules
├── manage.py                               # Django CLI entrypoint
├── requirements.txt                        # Python dependencies
├── Fuel_Route_Optimizer.postman_collection.json # Ready-to-import Postman collection
├── test_live_route_optimizer.py            # Live end-to-end integration test script
└── README.md                               # Project documentation
```

---

## 3. Data Cleaning & Coordinate Strategy

### 3.1 Fuel Data Cleaning (`optimizer/services_data.py`)
- **Raw File**: 8,151 rows and 7 columns.
- **Validation**: Verifies required columns (`OPIS Truckstop ID`, `Truckstop Name`, `Address`, `City`, `State`, `Rack ID`, `Retail Price`).
- **Deduplication**: 26 exact duplicate rows dropped (leaving 8,125 unique records).
- **Country Filter**: 620 non-US stations (Canadian provinces: ON, AB, BC, MB, SK, YT, QC, NS, NB) are excluded.
- **State Normalization**: Uppercase conversion and filtering against all 50 US States + District of Columbia.
- **Price Cleansing**: Prices converted to numeric; non-positive, zero, or missing prices removed.
- **Result**: Exactly **7,505 cleaned US stations** preserved.
- **Performance**: Thread-safe in-memory caching ensures the CSV is parsed only once per worker process.

### 3.2 Coordinate Enrichment & Verification (`optimizer/services/coordinates.py`)
The provided CSV does not contain geographic coordinates (`latitude`/`longitude`), and many addresses represent highway exit descriptors (e.g., `I-44, EXIT 283 & US-69`). Naive geocoding of such strings causes severe spatial anomalies.

Our multi-layered strategy solves this without rate limit exhaustion or false coordinate claims:
1. **Official US Census Bureau Gazetteer Places**:
   - Integrated the official US Census Bureau national place gazetteer containing over 64,000 incorporated places, towns, and census-designated places (CDPs).
   - Resolves **7,086+ stations (94.42%)** instantaneously offline.
2. **Strict Coordinate Validation**:
   - Every coordinate is validated against state-specific geographic bounding boxes for all 50 states + DC.
   - Exact station geocodes are validated against city centroids (distance must not exceed 45 miles), rejecting false highway exit matches.
3. **Honest Confidence & Source Metadata**:
   - `confidence`: `"exact"` vs `"city_centroid"` vs `"unresolved"`.
   - `is_exact`: `True` only when coordinates represent exact station locations; `False` for city centroids.
   - `source`: `"openrouteservice_pelias"`, `"us_census_gazetteer"`, `"cache"`.
4. **Persistent Local Storage**:
   - Dual-persistence in SQLite database table (`fuel_station_coordinates`) and JSON cache file (`data/station_coordinates_cache.json`).
   - Route requests perform **0 external geocoding calls for stations**, eliminating latency and upstream rate limits.
5. **Management Command**:
   - Run `python manage.py enrich_coordinates` to audit station coverage, populate database records, and optionally retry unresolved stations.

---

## 4. Fuel-Stop Optimization Algorithm

### 4.1 Problem Formulation
We model the route as a **Directed Acyclic Graph (DAG)** of refueling opportunities:
- **Node 0**: Origin (Route mile 0).
- **Nodes $1 \dots K$**: Candidate fuel stations situated along the corridor within `max_detour_miles` (default 15 miles).
- **Node $K+1$**: Destination (Route mile $D$).

### 4.2 Candidate Corridor Projection
Using spherical trigonometry (Haversine formula) and vectorized segment projection:
- Polyline coordinates from OpenRouteService are processed to calculate cumulative road miles from origin for every vertex.
- Bounding-box pre-filtering eliminates stations outside the route corridor in sub-millisecond time.
- For each bounding-box station, perpendicular distance to the closest route segment determines `detour_miles`, and position along the highway determines `route_mile`.
- Stations exceeding `max_detour_miles` are discarded.

### 4.3 Feasibility & Range Constraints
A directed edge exists from node $u$ to node $v$ ($u < v$) if and only if:
$$\text{distance}(u, v) = (d_v - d_u) + (2 \times \text{detour}_v) \le 500\text{ miles}$$
Because the vehicle can only refuel to full capacity (50 gallons = 500 miles range), no single driving segment may exceed 500 miles.

### 4.4 Global Cost Optimization vs Greedy Traps
A greedy algorithm that blindly picks the cheapest station within 500 miles can choose a station at mile 490 with a dead-end gap ahead, stranding the vehicle.

Our algorithm applies **Dynamic Programming / Topological Shortest Path**:
- Let $dp[v]$ be the minimum fuel expenditure to arrive at node $v$ with a full tank.
- For station $v$:
  $$\text{cost}(u, v) = \left(\frac{\text{distance}(u, v)}{10\text{ MPG}}\right) \times \text{Price}_v + \text{tie\_breaker}(\text{detour}_v, \text{stop\_penalty})$$
- For Destination $K+1$:
  $$\text{cost}(u, K+1) = 0\quad (\text{destination is reachable within remaining fuel})$$
- **Complexity**: $O(V + E)$ where $V \le 500$ candidates along a typical corridor and $E \le V^2 / 2$. Solves in **under 15 milliseconds**.
- If destination is unreachable across available station gaps, an explicit `InfeasibleRouteError` (HTTP 400) is returned.

### 4.5 Fuel Accounting Model
- **Initial Tank**: Vehicle starts at origin with a **full 50-gallon tank** (500 miles range).
- **Short Trips ($\le$ 500 miles)**: 0 stops required. Fuel consumed is $D / 10$ gallons, and purchase cost at stops is $\$0.00$.
- **Long Trips (> 500 miles)**:
  - At each selected stop $s_i$, the vehicle refuels fuel consumed since the previous fill:
    $$\text{Gallons Purchased}_i = \frac{\text{distance}(s_{i-1}, s_i)}{10\text{ MPG}}$$
    $$\text{Purchase Cost}_i = \text{Gallons Purchased}_i \times \text{Price}_i$$
  - The final destination is reached within the remaining fuel capacity from the last stop.
  - Total driven miles accounts for round-trip detours off the highway to stations:
    $$\text{Total Driven Miles} = \text{Main Route Distance} + \sum (2 \times \text{detour}_i)$$
    $$\text{Estimated Gallons Consumed} = \frac{\text{Total Driven Miles}}{10\text{ MPG}}$$

---

## 5. Setup & Installation

### Prerequisites
- Python 3.11, 3.12, 3.13, or 3.14
- OpenRouteService API Key (free at [openrouteservice.org](https://openrouteservice.org/dev/#/signup))

### Step 1: Clone and Create Virtual Environment
```bash
# Clone the repository
git clone <your-repo-url>
cd "Fuel Optimizer manual"

# Create a virtual environment
python -m venv venv

# Activate the virtual environment
# Windows (PowerShell):
.\venv\Scripts\Activate.ps1
# Linux / macOS:
source venv/bin/activate
```

### Step 2: Install Dependencies
```bash
pip install -r requirements.txt
```

### Step 3: Configure Environment Variables
Copy `.env.example` to `.env` and insert your OpenRouteService API key:
```ini
ORS_API_KEY=your_actual_openrouteservice_api_key_here
```

### Step 4: Run Database Migrations
```bash
python manage.py migrate
```

### Step 5: (Optional) Verify / Enrich Station Coordinates
The repository includes pre-generated Census coordinates in `data/station_coordinates_cache.json`. To sync with SQLite or view audit statistics:
```bash
python manage.py enrich_coordinates
```

### Step 6: Start the Development Server
```bash
python manage.py runserver
```
The API is now running at `http://127.0.0.1:8000/api/v1/`.

---

## 6. API Reference

### 6.1 Health Check
**Endpoint**: `GET /api/v1/`

**Response (`200 OK`)**:
```json
{
  "status": "success",
  "message": "Fuel Route Optimizer API is running!",
  "version": "v1",
  "endpoints": {
    "health": "/api/v1/",
    "route_optimize": "/api/v1/route-optimize/"
  }
}
```

---

### 6.2 Route & Fuel Optimization
**Endpoint**: `POST /api/v1/route-optimize/`

**Headers**:
```http
Content-Type: application/json
Accept: application/json
```

**Request Body (Short Route Sample)**:
```json
{
  "start": "Los Angeles, CA, USA",
  "destination": "Las Vegas, NV, USA"
}
```

**Response (`200 OK` - Short Route)**:
```json
{
  "status": "success",
  "route": {
    "start": "Los Angeles, CA, USA",
    "destination": "Las Vegas, NV, USA",
    "distance_miles": 279.92,
    "duration_seconds": 17116,
    "geometry": {
      "type": "LineString",
      "coordinates": [
        [-118.25703, 34.05513],
        [-118.25684, 34.05556],
        ...
        [-115.2791, 36.19036]
      ]
    }
  },
  "fuel_summary": {
    "mpg": 10.0,
    "max_range_miles": 500.0,
    "route_distance_miles": 279.92,
    "total_detour_miles": 0.0,
    "total_driven_miles": 279.92,
    "estimated_gallons_consumed": 27.99,
    "total_gallons_purchased": 0.0,
    "estimated_fuel_cost": 0.0,
    "stops_count": 0,
    "accounting_note": "Route is within the 500-mile range. No refueling stops required."
  },
  "fuel_stops": []
}
```

**Request Body (Long Cross-Country Route Sample)**:
```json
{
  "start": "Los Angeles, CA, USA",
  "destination": "Dallas, TX, USA",
  "max_detour_miles": 15.0
}
```

**Response (`200 OK` - Long Route)**:
```json
{
  "status": "success",
  "route": {
    "start": "Los Angeles, CA, USA",
    "destination": "Dallas, TX, USA",
    "distance_miles": 1443.92,
    "duration_seconds": 78920,
    "geometry": {
      "type": "LineString",
      "coordinates": [...]
    }
  },
  "fuel_summary": {
    "mpg": 10.0,
    "max_range_miles": 500.0,
    "route_distance_miles": 1443.92,
    "total_detour_miles": 25.88,
    "total_driven_miles": 1469.8,
    "estimated_gallons_consumed": 146.98,
    "total_gallons_purchased": 107.04,
    "estimated_fuel_cost": 301.84,
    "stops_count": 3,
    "accounting_note": "Vehicle begins with a full tank. At each stop, the vehicle refuels fuel consumed since the previous fill. Destination is reached within remaining tank range."
  },
  "fuel_stops": [
    {
      "stop_number": 1,
      "opis_id": 48318,
      "truckstop_name": "TCI PHOENIX",
      "address": "4220 W OSBORN RD",
      "city": "Phoenix",
      "state": "AZ",
      "price_per_gallon": 2.922,
      "route_mile": 372.77,
      "detour_miles": 7.65,
      "distance_from_last_stop": 388.07,
      "gallons_purchased": 38.81,
      "purchase_cost": 113.42,
      "coordinates": [-112.074, 33.4484],
      "coordinate_confidence": "city_centroid",
      "coordinate_source": "us_census_gazetteer",
      "is_exact": false
    },
    {
      "stop_number": 2,
      "opis_id": 78103,
      "truckstop_name": "DK",
      "address": "1200 E COMMERCIAL",
      "city": "El Paso",
      "state": "TX",
      "price_per_gallon": 2.699,
      "route_mile": 808.97,
      "detour_miles": 4.7,
      "distance_from_last_stop": 445.6,
      "gallons_purchased": 44.56,
      "purchase_cost": 120.27,
      "coordinates": [-106.485, 31.7619],
      "coordinate_confidence": "city_centroid",
      "coordinate_source": "us_census_gazetteer",
      "is_exact": false
    },
    {
      "stop_number": 3,
      "opis_id": 77800,
      "truckstop_name": "Main Street Market #112",
      "address": "201 N MAIN",
      "city": "Wickett",
      "state": "TX",
      "price_per_gallon": 2.879,
      "route_mile": 1044.53,
      "detour_miles": 0.59,
      "distance_from_last_stop": 236.74,
      "gallons_purchased": 23.67,
      "purchase_cost": 68.15,
      "coordinates": [-103.0064, 31.5701],
      "coordinate_confidence": "city_centroid",
      "coordinate_source": "us_census_gazetteer",
      "is_exact": false
    }
  ]
}
```

---

## 7. Testing & Verification

### 7.1 Automated Tests (Mocked External Calls)
Run all 27 automated tests:
```bash
python manage.py test
```
The test suite covers:
1. Health endpoint returns 200 OK.
2. Valid route request returns documented schema structure.
3. Missing origin rejected with 400.
4. Missing destination rejected with 400.
5. Empty strings and malformed JSON rejected with 400.
6. Geocoding failure returns 400 with location description.
7. Routing service timeout or 503 error returns 502 Bad Gateway.
8. Missing CSV columns and invalid fuel prices rejected.
9. Duplicate station records deduplicated.
10. Out-of-bounds coordinates rejected by state bounding box.
11. Journey shorter than range returns 0 stops.
12. Journey ~800 miles returns 1 optimal stop.
13. Journey ~1300 miles returns multiple optimal stops.
14. Infeasible gaps (> 500 mi) return explicit infeasible error.
15. Global DP chooses cost-optimal plan over local cheap dead-end trap.
16. Destination reachable without unnecessary additional stop.
17. Fuel consumption and purchase arithmetic accuracy.
18. Excessive detour stations (> max_detour_miles) excluded.
19. Repeated requests reuse cached geocoding/directions data.
20. Single directions API call made per route request.

### 7.2 Live Integration Test
Verify against live OpenRouteService APIs:
```bash
python test_live_route_optimizer.py
```

---

## 8. Postman Collection

Import `Fuel_Route_Optimizer.postman_collection.json` into Postman to run pre-configured test requests:
1. `1. API Health Check` (`GET http://127.0.0.1:8000/api/v1/`)
2. `2. Short Route (< 500 mi)` (`POST http://127.0.0.1:8000/api/v1/route-optimize/`)
3. `3. Long Route (> 500 mi)` (`POST http://127.0.0.1:8000/api/v1/route-optimize/`)
4. `4. Validation Error - Missing Destination` (`POST http://127.0.0.1:8000/api/v1/route-optimize/`)
5. `5. Validation Error - Identical Locations` (`POST http://127.0.0.1:8000/api/v1/route-optimize/`)

---

## 9. 5-Minute Video Demonstration Script

| Timestamp | Section | Key Talking Points |
| :--- | :--- | :--- |
| **0:00 - 0:45** | **1. Problem & Architecture** | Introduce Fuel Route Optimizer. Explain constraints: 500-mile tank range, 10 MPG, US-only, real OPIS fuel prices. Show clean Django project structure (`services/`, `models.py`, `views.py`). |
| **0:45 - 1:30** | **2. Coordinate Solution** | Explain the coordinate problem with highway exit addresses (`I-44, EXIT 283`). Demonstrate Census Gazetteer integration, state bounding box validation, and offline caching (94.4%+ resolved, 0 live calls per station). |
| **1:30 - 2:30** | **3. Short Journey Demo (< 500 mi)** | Send POST request in Postman for Los Angeles to Las Vegas (279.92 mi). Show `status: success`, GeoJSON LineString geometry, 27.99 gal consumed, and 0 stops needed because the vehicle starts with a full tank. |
| **2:30 - 3:45** | **4. Long Journey Demo (> 500 mi)** | Send POST request for Los Angeles to Dallas (1,443.92 mi). Show 3 optimal fuel stops selected via Global DP (Phoenix AZ, El Paso TX, Wickett TX). Point out real CSV prices, detour miles, and leg distances all $\le 500$ miles. |
| **3:45 - 4:30** | **5. Tests & Edge Cases** | Run `python manage.py test`. Highlight all 27 tests passing: range limits, global choice vs greedy trap, excessive detour filtering, 502 upstream errors, and single-routing-call efficiency. |
| **4:30 - 5:00** | **6. Conclusion & Submission** | Highlight that `.env` and `db.sqlite3` are securely excluded from Git, `.env.example` is documented, and the codebase is production-ready for GitHub. |

---

## 10. Known Limitations & Assumptions

1. **Initial Tank State**: Assumes the vehicle departs the origin with a full 50-gallon tank (500 miles range).
2. **Station Coordinate Granularity**: Where exact building-level coordinates are unavailable due to highway exit address descriptions, stations default to official US Census Bureau city centroids (`confidence: "city_centroid"`, `is_exact: false`). This is explicitly documented in the API response metadata.
3. **Detour Modeling**: Detour distance is calculated as perpendicular distance from highway corridor to the station. Round-trip detour is modeled as $2 \times \text{detour}$.
4. **Fuel Prices**: Retail prices reflect the snapshot provided in `data/fuel-prices-for-be-assessment.csv`.
