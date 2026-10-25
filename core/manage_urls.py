"""Management-only routes owned by core."""

from django.urls import path

from . import views

app_name = "analytics"

urlpatterns = [
    path("", views.visit_dashboard, name="visits"),
]
