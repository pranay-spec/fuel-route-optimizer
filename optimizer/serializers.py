from rest_framework import serializers


class VehicleSerializer(serializers.Serializer):
    max_range_miles = serializers.FloatField(required=False, default=500.0, min_value=50.0, max_value=2000.0)
    miles_per_gallon = serializers.FloatField(required=False, default=10.0, min_value=1.0, max_value=100.0)
    start_with_full_tank = serializers.BooleanField(required=False, default=True)


class RouteRequestSerializer(serializers.Serializer):
    start = serializers.CharField(
        required=True,
        allow_blank=False,
        trim_whitespace=True,
        min_length=2,
        max_length=255,
        error_messages={
            "required": "Starting location 'start' is required.",
            "blank": "Starting location 'start' cannot be blank.",
        },
    )

    destination = serializers.CharField(
        required=False,
        allow_blank=True,
        trim_whitespace=True,
        min_length=2,
        max_length=255,
    )

    finish = serializers.CharField(
        required=False,
        allow_blank=True,
        trim_whitespace=True,
        min_length=2,
        max_length=255,
        help_text="Alias for 'destination'.",
    )

    max_detour_miles = serializers.FloatField(
        required=False,
        default=15.0,
        min_value=1.0,
        max_value=100.0,
        help_text="Maximum allowed detour distance in miles off the primary route (default: 15.0).",
    )

    vehicle = VehicleSerializer(required=False)

    def validate(self, attrs):
        start = attrs.get("start", "").strip()
        destination = attrs.get("destination", "").strip() or attrs.get("finish", "").strip()

        if not start:
            raise serializers.ValidationError({"start": "Starting location 'start' cannot be blank."})
        if not destination:
            raise serializers.ValidationError({"destination": "Destination location 'destination' is required."})

        if start.lower() == destination.lower():
            raise serializers.ValidationError(
                {"destination": "Destination location must be different from starting location."}
            )

        attrs["destination"] = destination
        return attrs
