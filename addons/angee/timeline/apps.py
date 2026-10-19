"""Django identity for the record timeline."""

from django.apps import AppConfig


class TimelineConfig(AppConfig):
    default = True
    name = "angee.timeline"
