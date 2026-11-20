"""AppConfig for the web forms consumer probe (test-only installed app).

Registered in ``tests.settings`` so the permission schema carries what a consumer
addon declares for its target: the type the ``tag`` target of
``tests/test_webforms_responses.py`` makes, as a relation on
``webforms/response_record`` from ``permissions.extends.zed``. It has no model and
no addon entry point; the composer and schema discovery ignore it.
"""

from __future__ import annotations

from django.apps import AppConfig


class WebformsDemoConfig(AppConfig):
    """Installed app hosting the web forms target declaration."""

    name = "tests.webformsdemo"
    label = "webformsdemo"
