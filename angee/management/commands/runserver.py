"""Serve the composed ASGI app with uvicorn under Django's autoreloader.

Mirrors Daphne's ``runserver`` override: subclass Django's ``RunserverCommand``
and keep its native follow-imports autoreloader. The host closes Uvicorn's
lifecycle when the inherited ``run()`` exits, before Django's reload exit code
leaves non-daemon ASGI work behind. Serving with uvicorn keeps the ASGI lifespan
the MCP mount needs
(see :mod:`angee.asgi`); uvicorn no-ops its own signal handlers off the main
thread, so it composes with the reloader's worker thread exactly as Daphne's
server does. It wins over ``django.core``'s base ``runserver`` because the
host settings place ``angee`` before competing commands in ``INSTALLED_APPS`` (Django's
``get_commands`` lets the earliest app win) including ``staticfiles``/Daphne ``runserver``.
"""

from __future__ import annotations

from threading import Thread, current_thread
from typing import Any

import uvicorn
from django.conf import settings
from django.core.management.commands.runserver import Command as RunserverCommand
from django.utils import autoreload
from django.utils.module_loading import import_string


class Command(RunserverCommand):
    """Let Django own reload decisions and Uvicorn own ASGI shutdown."""

    # Django's handle() resolves these before dispatching run()/inner_run().
    addr: str
    port: str

    _server: uvicorn.Server | None = None
    _server_thread: Thread | None = None

    def run(self, **options: Any) -> None:
        """Close the ASGI lifecycle only after Django decides to leave its run loop.

        Handled file-change signals keep running natively. On an actual reload,
        Django's SystemExit unwinds through here before interpreter shutdown;
        Uvicorn can therefore close connections and release lifespan-owned
        threads instead of leaving Python waiting for those threads to exit.
        """

        self._server = None
        self._server_thread = None
        try:
            super().run(**options)
        finally:
            if self._server is not None:
                self._server.should_exit = True
                if self._server_thread is not None and self._server_thread is not current_thread():
                    self._server_thread.join()

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
        application = import_string(settings.ASGI_APPLICATION)
        self._server = uvicorn.Server(
            uvicorn.Config(
                application,
                host=self.addr,
                port=int(self.port),
                reload=False,
            )
        )
        self._server_thread = current_thread()
        self._server.run()
