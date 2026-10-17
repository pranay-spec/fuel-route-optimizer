import time
from typing import Any
from django.core.management.base import BaseCommand
from optimizer.models import FuelStationCoordinate
from optimizer.services_data import load_fuel_stations
from optimizer.services.coordinates import (
    enrich_all_stations,
    load_persistent_station_cache,
    save_persistent_station_cache,
    validate_coordinates,
)
from optimizer.services.routing import geocode_location


class Command(BaseCommand):
    help = "Enrich fuel stations with verified or Census centroid coordinates and populate the database cache."

    def add_arguments(self, parser):
        parser.add_argument(
            "--resolve-unresolved",
            action="store_true",
            help="Attempt live Pelias geocoding for unresolved stations (subject to API rate limits).",
        )
        parser.add_argument(
            "--max-live-queries",
            type=int,
            default=30,
            help="Maximum number of external live queries to perform in one run (default: 30).",
        )

    def handle(self, *args: Any, **options: Any):
        self.stdout.write(self.style.NOTICE("Loading fuel station dataset..."))
        stations = load_fuel_stations()
        self.stdout.write(f"Loaded {len(stations)} cleaned US stations.")

        self.stdout.write("Enriching stations from Census Gazetteer and cache...")
        enriched = enrich_all_stations(stations)

        resolve_unresolved = options.get("resolve_unresolved", False)
        max_live = options.get("max_live_queries", 30)

        cache = load_persistent_station_cache()

        if resolve_unresolved:
            unresolved_stations = [s for s in enriched if s["status"] == "unresolved"]
            self.stdout.write(
                self.style.WARNING(
                    f"Found {len(unresolved_stations)} unresolved stations. Attempting up to {max_live} live queries..."
                )
            )

            resolved_count = 0
            for st in unresolved_stations[:max_live]:
                query = f"{st['City']}, {st['State']}, USA"
                self.stdout.write(f"Querying Pelias for: {query}")
                try:
                    coords = geocode_location(query)
                    lon, lat = coords[0], coords[1]
                    valid, note = validate_coordinates(lat, lon, st["State"])
                    if valid:
                        key = f"{st['OPIS Truckstop ID']}_{st['Address'].strip()}_{st['City'].strip()}_{st['State'].strip()}".lower()
                        entry = {
                            "latitude": lat,
                            "longitude": lon,
                            "confidence": "city_approximate",
                            "source": "openrouteservice_pelias",
                            "is_exact": False,
                            "status": "approximate",
                            "validation_notes": "Live Pelias city geocoding",
                        }
                        cache[key] = entry
                        st.update(entry)
                        resolved_count += 1
                        self.stdout.write(self.style.SUCCESS(f"  -> Resolved to [{lon:.4f}, {lat:.4f}]"))
                    else:
                        self.stdout.write(self.style.ERROR(f"  -> Invalid coordinates returned: {note}"))
                except Exception as exc:
                    self.stdout.write(self.style.ERROR(f"  -> Failed: {exc}"))

                # Respect rate limits (at least 1.5s between requests)
                time.sleep(1.5)

            self.stdout.write(self.style.SUCCESS(f"Live resolved {resolved_count} stations."))
            save_persistent_station_cache(cache)

        # Save to database
        self.stdout.write("Syncing coordinate records with database table 'fuel_station_coordinates'...")
        records_to_create = []
        existing_keys = set(
            FuelStationCoordinate.objects.values_list("opis_id", "address", "city", "state")
        )

        for s in enriched:
            key_tuple = (s["OPIS Truckstop ID"], s["Address"], s["City"], s["State"])
            if key_tuple not in existing_keys:
                existing_keys.add(key_tuple)
                records_to_create.append(
                    FuelStationCoordinate(
                        opis_id=s["OPIS Truckstop ID"],
                        truckstop_name=s["Truckstop Name"],
                        address=s["Address"],
                        city=s["City"],
                        state=s["State"],
                        latitude=s.get("latitude"),
                        longitude=s.get("longitude"),
                        confidence=s.get("confidence", "unresolved"),
                        source=s.get("source", "none"),
                        is_exact=s.get("is_exact", False),
                        status=s.get("status", "unresolved"),
                        validation_notes=s.get("validation_notes", ""),
                    )
                )

        if records_to_create:
            FuelStationCoordinate.objects.bulk_create(records_to_create, batch_size=1000)
            self.stdout.write(self.style.SUCCESS(f"Created {len(records_to_create)} database records."))
        else:
            self.stdout.write("Database records are already up to date.")

        # Summary Statistics
        total = len(enriched)
        exact = sum(1 for s in enriched if s.get("is_exact", False))
        approx = sum(1 for s in enriched if s.get("status") == "approximate")
        unresolved = sum(1 for s in enriched if s.get("status") == "unresolved")

        self.stdout.write("\n" + "=" * 50)
        self.stdout.write(self.style.SUCCESS("COORDINATE ENRICHMENT AUDIT REPORT"))
        self.stdout.write("=" * 50)
        self.stdout.write(f"Total Cleaned Stations: {total}")
        self.stdout.write(f"Exact / Verified:       {exact}")
        self.stdout.write(f"Approximate / Centroid: {approx} ({approx/total*100:.2f}%)")
        self.stdout.write(f"Unresolved / Excluded:  {unresolved} ({unresolved/total*100:.2f}%)")
        self.stdout.write("=" * 50)
