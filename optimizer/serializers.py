from rest_framework import serializers


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
        required=True,
        allow_blank=False,
        trim_whitespace=True,
        min_length=2,
        max_length=255,
        error_messages={
            "required": "Destination location 'destination' is required.",
            "blank": "Destination location 'destination' cannot be blank.",
        },
    )

    max_detour_miles = serializers.FloatField(
        required=False,
        default=15.0,
        min_value=1.0,
        max_value=100.0,
        help_text="Maximum allowed detour distance in miles off the primary route (default: 15.0).",
    )

    def validate(self, attrs):
        start = attrs.get("start", "").strip()
        destination = attrs.get("destination", "").strip()

        if not start:
            raise serializers.ValidationError({"start": "Start location cannot be empty."})
        if not destination:
            raise serializers.ValidationError({"destination": "Destination location cannot be empty."})

        if start.lower() == destination.lower():
            raise serializers.ValidationError(
                {"destination": "Destination location must be different from starting location."}
            )

        return attrs
