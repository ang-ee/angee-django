"""Native Celery application for Angee jobs.

``ANGEE_CELERY_TASK_CLASS`` optionally selects Celery's app-wide task base by
class path. Without that contribution Celery retains its native Task.
"""

from __future__ import annotations

import os

from celery import Celery
from django.conf import settings

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "angee.settings")


app = Celery("angee", task_cls=getattr(settings, "ANGEE_CELERY_TASK_CLASS", "celery.app.task:Task"))
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
