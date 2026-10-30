"""Django identity and explicit HTTP routes for the serving capability."""

from django.apps import AppConfig


class ServerConfig(AppConfig):
    name = "angee.server"
    default = True
    compose_urls = "angee.server.urls"
