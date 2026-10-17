from django.db import models


class FuelStationCoordinate(models.Model):
    """
    Persistent store for fuel station geographic coordinates and enrichment metadata.
    Prevents repeated external geocoding and ensures coordinates are strictly validated.
    """
    opis_id = models.IntegerField(db_index=True, help_text="OPIS Truckstop ID from CSV")
    truckstop_name = models.CharField(max_length=255)
    address = models.CharField(max_length=255)
    city = models.CharField(max_length=128, db_index=True)
    state = models.CharField(max_length=10, db_index=True)

    # Coordinates in WGS84: GeoJSON order is [longitude, latitude]
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)

    # Confidence: 'exact', 'city_centroid', 'approximate', 'unresolved'
    confidence = models.CharField(max_length=50, default="unresolved")

    # Source: 'openrouteservice_pelias', 'us_census_gazetteer', 'cache', 'manual'
    source = models.CharField(max_length=100, default="none")

    # Flag: True ONLY when coordinates represent the actual station, not a city centroid
    is_exact = models.BooleanField(default=False)

    # Status: 'verified', 'approximate', 'unresolved', 'failed'
    status = models.CharField(max_length=50, default="unresolved")

    validation_notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "fuel_station_coordinates"
        indexes = [
            models.Index(fields=["state", "city"]),
            models.Index(fields=["opis_id"]),
            models.Index(fields=["status"]),
        ]
        unique_together = ("opis_id", "address", "city", "state")

    def __str__(self):
        return f"{self.truckstop_name} - {self.city}, {self.state} ({self.confidence})"
