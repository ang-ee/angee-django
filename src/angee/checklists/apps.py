"""Django identity for checklists."""

from django.apps import AppConfig


class ChecklistsConfig(AppConfig):
    default = True
    name = "angee.checklists"
    requires_apps = ("django.contrib.contenttypes",)
