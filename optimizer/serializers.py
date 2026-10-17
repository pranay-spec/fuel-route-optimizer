from rest_framework import serializers


class RouteRequestSerializer(serializers.Serializer):
    start = serializers.CharField(
        required=True,
        allow_blank=False,
        trim_whitespace=True
    )

    destination = serializers.CharField(
        required=True,
        allow_blank=False,
        trim_whitespace=True
    )
