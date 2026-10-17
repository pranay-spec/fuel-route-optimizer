
from django.urls import path
from . import views

urlpatterns = [
    path("v1/", views.home, name="api-home"),
    path(
        "v1/route-optimize/",
        views.route_optimize,
        name="route-optimize"
    ),
]
