"""Prepare database state for an already composed application."""
from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandParser
from django.db import DEFAULT_DB_ALIAS

from angee.base.provision import run


class Command(BaseCommand):
    help = "Apply migrations and idempotent addon database hooks for the verified composition."
    requires_system_checks: list[str] = []

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--database", default=DEFAULT_DB_ALIAS)
        parser.add_argument("--wait-db", type=int, default=60, metavar="SECONDS")
        parser.add_argument("--check", action="store_true", help="Read readiness without changing database state.")

    def handle(self, *args: Any, **options: Any) -> None:
        run(self, options)
