"""Django identity for decisions."""

from django.apps import AppConfig


class DecisionsConfig(AppConfig):
    default = True
    name = "angee.decisions"
    requires_apps = ('django.contrib.contenttypes',)
