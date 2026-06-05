"""Seed bulk lorem-ipsum notes for volume / performance e2e testing.

e2e drives a running stack, so volume data is seeded server-side, not through the
browser. Three model facts shape the implementation:

* ``created_at`` / ``updated_at`` are ``auto_now_add`` / ``auto_now``
  (``angee.base.mixins``), so they cannot be set on insert — they are backfilled
  with a second ``bulk_update``.
* Ownership is field-backed by ``created_by`` and admin reach is const-backed
  via ``angee/role:admin`` in ``permissions.zed``, so a note carries no per-row
  REBAC tuples — bulk insert writes none.
* Generation is seeded (``Faker.seed_instance``) so a run is reproducible.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management.base import (
    BaseCommand,
    CommandError,
    CommandParser,
)
from faker import Faker
from rebac import system_context

_TAGS = (
    "idea",
    "todo",
    "draft",
    "review",
    "archive",
    "urgent",
    "personal",
    "work",
)


def _midnight(value: str) -> datetime:
    """Parse ``YYYY-MM-DD`` into a UTC datetime at the start of that day."""

    return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)


class Command(BaseCommand):
    """Seed lorem-ipsum notes dated across a range, owned by one user."""

    help = "Seed N lorem-ipsum notes dated across a range, owned by a user."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--count", type=int, default=10_000)
        parser.add_argument("--owner", default="alice", help="username that owns the notes")
        parser.add_argument(
            "--start",
            default="2021-01-01",
            help="earliest note date (YYYY-MM-DD)",
        )
        parser.add_argument("--end", default="2023-12-31", help="latest note date (YYYY-MM-DD)")
        parser.add_argument(
            "--seed",
            type=int,
            default=42,
            help="Faker seed for reproducibility",
        )
        parser.add_argument("--batch", type=int, default=1_000, help="rows per insert batch")
        parser.add_argument(
            "--fresh",
            action="store_true",
            help="delete the owner's notes first",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        count: int = options["count"]
        batch: int = options["batch"]
        start = _midnight(options["start"])
        end = _midnight(options["end"])
        if end <= start:
            raise CommandError("--end must be after --start")
        if count < 0:
            raise CommandError("--count must not be negative")

        note_model = apps.get_model("notes", "Note")
        user_model = get_user_model()
        statuses = [value for value, _label in note_model._meta.get_field("status").flatchoices]

        faker = Faker()
        faker.seed_instance(options["seed"])

        with system_context(reason="seed_lorem_notes"):
            try:
                owner = user_model.objects.get(username=options["owner"])
            except user_model.DoesNotExist as exc:
                raise CommandError(f"owner {options['owner']!r} not found — load demo data first") from exc

            if options["fresh"]:
                removed, _ = note_model.objects.filter(created_by=owner).delete()
                self.stdout.write(f"deleted {removed} existing notes for {options['owner']}")

            made = 0
            while made < count:
                size = min(batch, count - made)
                notes = [
                    note_model(
                        title=faker.sentence(nb_words=4).rstrip("."),
                        body=(body := "\n\n".join(faker.paragraphs(nb=3))),
                        word_count=note_model.count_words(body),
                        status=faker.random_element(statuses),
                        is_starred=faker.boolean(chance_of_getting_true=15),
                        tags=list(
                            faker.random_elements(
                                _TAGS,
                                length=faker.random_int(0, 3),
                                unique=True,
                            )
                        ),
                        created_by=owner,
                        updated_by=owner,
                    )
                    for _ in range(size)
                ]
                note_model.objects.bulk_create(notes)
                # Owner access is field-backed by created_by and admin reach is
                # const-backed via angee/role:admin, so bulk insert writes no
                # per-note REBAC tuples.
                # Backfill the timestamps auto_now_add/auto_now ignored on insert.
                for note in notes:
                    stamp = faker.date_time_between(start_date=start, end_date=end, tzinfo=timezone.utc)
                    note.created_at = stamp
                    note.updated_at = stamp
                note_model.objects.bulk_update(notes, ["created_at", "updated_at"])
                made += size

        self.stdout.write(
            self.style.SUCCESS(
                f"seeded {made} notes owned by {options['owner']} dated {options['start']}..{options['end']}"
            )
        )
