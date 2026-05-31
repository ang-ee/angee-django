"""Render and check generated GraphQL SDL files."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import (
    BaseCommand,
    CommandError,
    CommandParser,
)

from angee.base.graphql.schema import GraphQLSchemas


class Command(BaseCommand):
    """Expose GraphQL SDL rendering through Django management."""

    help = "Write or check generated GraphQL SDL output."
    requires_system_checks: list[str] = []

    def add_arguments(self, parser: CommandParser) -> None:
        """Add schema command arguments."""

        parser.add_argument("--check", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        """Write or check rendered GraphQL SDL files."""

        del args
        try:
            if options["check"]:
                self._check_schema_sdl()
                message = "schema --check: ok"
            else:
                self._write_schema_sdl()
                message = "schema: ok"
        except RuntimeError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(self.style.SUCCESS(message))

    def _write_schema_sdl(self) -> None:
        """Write rendered SDL files under the configured runtime directory."""

        for name, sdl in self._render_schema_sdl().items():
            self._write_file(self._schema_dir() / f"{name}.graphql", sdl)

    def _check_schema_sdl(self) -> None:
        """Raise when rendered SDL differs from runtime schema files."""

        expected = self._render_schema_sdl()
        schema_dir = self._schema_dir()
        actual = (
            {
                path.stem: path.read_text(encoding="utf-8")
                for path in sorted(schema_dir.glob("*.graphql"))
            }
            if schema_dir.exists()
            else {}
        )
        drift = sorted(
            (set(expected) ^ set(actual))
            | {
                name
                for name in expected.keys() & actual.keys()
                if expected[name] != actual[name]
            }
        )
        if drift:
            rendered = ", ".join(f"schemas/{name}.graphql" for name in drift)
            raise RuntimeError(f"generated GraphQL SDL is stale: {rendered}")

    def _render_schema_sdl(self) -> dict[str, str]:
        """Return printed GraphQL SDL per schema name."""

        return GraphQLSchemas.from_discovery().render_sdl()

    def _schema_dir(self) -> Path:
        """Return the runtime schema output directory."""

        return Path(settings.ANGEE_RUNTIME_DIR) / "schemas"

    def _write_file(self, path: Path, text: str) -> None:
        """Write ``text`` to ``path`` when contents changed."""

        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.read_text(encoding="utf-8") == text:
            return
        path.write_text(text, encoding="utf-8")
