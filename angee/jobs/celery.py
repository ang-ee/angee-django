"""Celery application for Angee job execution."""

from __future__ import annotations

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "angee.compose.settings")

app = Celery("angee")
app.conf.update(
    task_ignore_result=True,
    task_track_started=True,
    task_time_limit=900,
    task_soft_time_limit=840,
)
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
