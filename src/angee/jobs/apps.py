"""Native Django dependencies for angee.jobs."""

from django.apps import AppConfig


class JobsConfig(AppConfig):
    name = 'angee.jobs'
    requires_apps = ('django_celery_beat',)
