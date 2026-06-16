"""Serve the composed ASGI app with uvicorn under Django's autoreloader.

Mirrors Daphne's ``runserver`` override: subclass Django's ``RunserverCommand``
and override only :meth:`inner_run`, inheriting the base ``run()`` that wraps it
in :func:`django.utils.autoreload.run_with_reloader` (the follow-imports
autoreloader). Serving with uvicorn keeps the ASGI lifespan the MCP mount needs
(see :mod:`angee.asgi`); uvicorn no-ops its own signal handlers off the main
thread, so it composes with the reloader's worker thread exactly as Daphne's
server does. It wins over ``django.core``'s base ``runserver`` because the
composer pins ``angee.compose`` first in ``INSTALLED_APPS`` (Django's
``get_commands`` lets the earliest app win) and the composed runtime ships no
competing ``staticfiles``/Daphne ``runserver``.
"""

from __future__ import annotations

import os
from typing import Any

import uvicorn
from django.conf import settings
from django.core.management.commands.runserver import Command as RunserverCommand
from django.utils import autoreload
from django.utils.module_loading import import_string


class Command(RunserverCommand):
    """Run the composed ASGI app with uvicorn; the base ``run()`` owns reloading."""

    def inner_run(self, *args: Any, **options: Any) -> None:
        """Serve ``ASGI_APPLICATION`` with uvicorn (no uvicorn-side reload).

        Runs the same boot gate Django's/Daphne's ``inner_run`` do —
        ``raise_last_exception`` then system + migration checks — so a config error
        (e.g. an ``ImplClassField`` registry path or REBAC misconfig the guidelines
        validate via ``check``) surfaces at startup, not on the first request. The
        base ``run()`` owns the reload decision, so ``args`` is unused.
        """

        del args
        autoreload.raise_last_exception()
        if not options.get("skip_checks"):
            self.stdout.write("Performing system checks...\n\n")
            self.check(display_num_errors=True)
        self.check_migrations()
        # The dev SDL boot hook in angee.asgi regenerates the GraphQL SDL on each
        # reloader child; set its gate before the app is imported just below.
        os.environ["ANGEE_DEV_SDL"] = "1"
        application = import_string(settings.ASGI_APPLICATION)
        uvicorn.Server(
            uvicorn.Config(
                application,
                host=self.addr,
                port=int(self.port),
                reload=False,
            )
        ).run()
