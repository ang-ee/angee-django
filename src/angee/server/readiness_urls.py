"""Readiness route contributed through the server URL contract."""

from django.urls import path

from angee.server.views import readiness

urlpatterns = [path("health/ready", readiness, name="angee-server-readiness")]
