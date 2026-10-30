"""Serve the composed ASGI application with production uvicorn workers."""

from __future__ import annotations

import os
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    """Run the composed ASGI app without the development autoreloader."""

    help = "Serve the composed ASGI application with production workers."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--require-provisioned", action="store_true")
        parser.add_argument("--database", default="default")
        parser.add_argument("--wait-db", type=float, default=60)
        parser.add_argument("--workers", type=int, default=0, help="Worker processes; 0 uses CPU count (at least 2).")
        parser.add_argument("--production", action="store_true", help="Require at least two worker processes.")
        parser.add_argument("--host", default="127.0.0.1")
        parser.add_argument("--port", type=int, default=8000)
        parser.add_argument("--forwarded-allow-ips", default="127.0.0.1")

    def handle(self, **options: Any) -> None:
        import uvicorn
        from django_compose.provision import require_provisioned

        if options["require_provisioned"]:
            require_provisioned(using=options["database"], timeout=options["wait_db"])
        workers = options["workers"]
        if workers < 0 or (options["production"] and workers != 0 and workers < 2):
            raise CommandError("Production serving requires at least two workers, or 0 for CPU count.")
        workers = workers or max(2, os.cpu_count() or 2)
        module, _, attribute = settings.ASGI_APPLICATION.rpartition(".")
        uvicorn.run(
            f"{module}:{attribute}",
            host=options["host"],
            port=options["port"],
            workers=workers,
            proxy_headers=True,
            forwarded_allow_ips=options["forwarded_allow_ips"],
        )
