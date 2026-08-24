"""Celery application for Angee job execution."""

from __future__ import annotations

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "angee.compose.settings")

app = Celery("angee")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
