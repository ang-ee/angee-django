"""Sequence: gapless, period-resetting document numbering.

A :class:`Sequence` is a named counter with a formatting template; a
:class:`SequenceCounter` holds the current value for one ``(sequence, period)``
pair. Consumers seed their ``Sequence`` rows and compose scope into the key —
the framework stays scope-agnostic (``"<addon>.<document>/<company_sqid>"``
is a consumer convention, not a sequence concern).

Allocation protocol (``Sequence.objects.next_value``), executed **exactly**:

1. resolve the ``Sequence`` by key (fail fast — ``Sequence.DoesNotExist``);
2. compute the period key from ``period_reset`` and the draw date;
3. ``SELECT … FOR UPDATE`` the counter row (:meth:`AngeeQuerySet.lock_if_supported`);
4. if it is absent, ``INSERT … ON CONFLICT DO NOTHING``
   (``bulk_create(ignore_conflicts=True)``) then re-``SELECT … FOR UPDATE`` —
   the winner and the loser of the first-draw race both end up locking the one
   row;
5. increment the counter and format the value through the template.

The counter reads and writes run under ``system_context`` (framework-owned
bookkeeping the actor never touches directly); ``next_value`` never opens
its own transaction, so it **must** be called inside the caller's transaction:
rollback releases the row lock and never burns a number, and concurrent posts
serialize on the lock.

**Backend scope of the gapless guarantee.** Gaplessness is a PostgreSQL fact,
resting on ``SELECT … FOR UPDATE``. ``lock_if_supported`` is a no-op on backends
without row locks (the SQLite dev floor), so the same code runs there *without*
the lock and the invariant is **not** guaranteed. The concurrency test is
PostgreSQL-marked accordingly.
"""

from __future__ import annotations

from datetime import date

from django.apps import apps
from django.db import models
from django.utils import timezone
from rebac import system_context

from angee.base.fields import StateField
from angee.base.models import AngeeDataModel, AngeeManager, AngeeModel, role_anchor


class PeriodReset(models.TextChoices):
    """When a sequence restarts its counter at 1."""

    NONE = "none", "No reset"
    YEAR = "year", "Yearly"
    MONTH = "month", "Monthly"


class SequenceManager(AngeeManager):
    """Draws and previews formatted numbers from a keyed sequence."""

    def next_value(self, key: str, *, on_date: date | None = None) -> str:
        """Reserve and return the next formatted number for ``key``.

        Runs the module's allocation protocol under ``system_context``: resolves
        the sequence (raising :class:`Sequence.DoesNotExist` when the key is
        unknown), draws the locked counter for the resolved period, and formats
        the incremented value. Call this **inside the caller's transaction**
        (see the module docstring): the row lock is held until that transaction
        commits, so a rollback returns the number and concurrent draws serialize.
        """

        draw_date = on_date or timezone.localdate()
        with system_context(reason="sequence.next_value"):
            sequence = self.get(key=key)
            period = sequence.period_key(draw_date)
            value = self._counter_model().objects.draw(sequence, period)
            return sequence.format_number(value, draw_date)

    def preview_next(self, key: str, *, on_date: date | None = None) -> str | None:
        """Return the number ``next_value`` would draw, without reserving it.

        A non-locking, non-reserving peek for draft forms: returns ``None``
        unless the sequence exists and has ``preview_enabled``. It is
        **advisory** — a concurrent post may take the previewed number before the
        draft is saved, so a stale preview is correct behaviour, not a bug.
        Nothing may treat the result as a reservation; use :meth:`next_value` for
        the authoritative, fail-fast draw.
        """

        draw_date = on_date or timezone.localdate()
        with system_context(reason="sequence.preview_next"):
            sequence = self.filter(key=key).first()
            if sequence is None or not sequence.preview_enabled:
                return None
            period = sequence.period_key(draw_date)
            current = self._counter_model().objects.peek(sequence, period)
            return sequence.format_number(current + 1, draw_date)

    def _counter_model(self) -> type[models.Model]:
        """Return the sibling counter model through the app registry."""

        return apps.get_model(self.model._meta.app_label, "SequenceCounter")


class Sequence(AngeeDataModel):
    """A named counter with a formatting template and reset period.

    The ``key`` is the stable lookup handle consumers draw against; scope (a
    company, a journal) is composed into the key by the consumer, keeping the
    sequence framework itself scope-agnostic. ``template`` is a ``str.format``
    pattern over ``{prefix}``, ``{year}``, ``{month}`` and ``{number}`` (e.g.
    ``"INV/{year}/{number:05d}"``).
    """

    runtime = True
    sqid_prefix = "seq_"

    key = models.CharField(max_length=200, unique=True)
    name = models.CharField(max_length=200)
    template = models.CharField(max_length=200)
    prefix = models.CharField(max_length=32, blank=True, default="")
    period_reset = StateField(choices_enum=PeriodReset, default=PeriodReset.NONE)
    preview_enabled = models.BooleanField(default=False)

    objects = SequenceManager()

    class Meta:
        """Django model options for a sequence."""

        abstract = True
        ordering = ("key",)
        rebac_resource_type = "sequence/sequence"
        rebac_id_attr = "sqid"

    def __str__(self) -> str:
        """Return the sequence key for Django displays."""

        return self.key

    def period_key(self, on_date: date) -> str:
        """Return the counter partition key for ``on_date`` under this reset.

        ``""`` when the counter never resets, ``"2026"`` for a yearly reset, and
        ``"2026-07"`` for a monthly reset.
        """

        if self.period_reset == PeriodReset.YEAR:
            return f"{on_date.year:04d}"
        if self.period_reset == PeriodReset.MONTH:
            return f"{on_date.year:04d}-{on_date.month:02d}"
        return ""

    def format_number(self, value: int, on_date: date) -> str:
        """Render ``value`` for ``on_date`` through this sequence's template."""

        return self.template.format(
            prefix=self.prefix,
            year=on_date.year,
            month=on_date.month,
            number=value,
        )


class SequenceCounterManager(AngeeManager):
    """Owns the locked counter row for one ``(sequence, period)`` pair.

    Not a GraphQL surface: counter rows are internal bookkeeping the caller never
    addresses directly. Callers reach these methods through
    :class:`SequenceManager`, which brackets them in ``system_context``.
    """

    def draw(self, sequence: models.Model, period: str) -> int:
        """Lock, create-if-absent, increment and return this counter's value.

        Steps 3–5 of the allocation protocol. ``lock_if_supported`` applies
        ``SELECT … FOR UPDATE`` where the backend supports it (a no-op on the
        SQLite floor). On the first draw of a new period the locked read finds
        nothing to lock, so both racers ``INSERT … ON CONFLICT DO NOTHING`` and
        re-read under the lock — landing on the one row and serializing there.
        """

        row = self.lock_if_supported().filter(sequence=sequence, period=period).first()
        if row is None:
            self.bulk_create(
                [self.model(sequence=sequence, period=period, value=0)],
                ignore_conflicts=True,
            )
            # Post-insert the row is guaranteed; ``locked_get`` re-locks it and
            # fails loudly if the invariant is ever violated (a deleted counter
            # mid-draw), rather than silently returning None.
            row = self.locked_get(sequence=sequence, period=period)
        row.value += 1
        row.save(update_fields=["value", "updated_at"])
        return row.value

    def peek(self, sequence: models.Model, period: str) -> int:
        """Return the current value for this pair without locking, ``0`` if none."""

        return self.filter(sequence=sequence, period=period).values_list("value", flat=True).first() or 0


class SequenceCounter(AngeeModel):
    """Current value for one sequence within one reset period.

    Internal to the sequence addon — no sqid, no GraphQL surface, no REBAC
    resource type. The ``(sequence, period)`` uniqueness is the row-lock target
    and the ``ON CONFLICT`` arbiter of the first-draw race.
    """

    runtime = True

    sequence = models.ForeignKey(
        "sequence.Sequence",
        on_delete=models.CASCADE,
        related_name="counters",
    )
    period = models.CharField(max_length=16, blank=True, default="")
    value = models.BigIntegerField(default=0)

    objects = SequenceCounterManager()

    class Meta:
        """Django model options for a sequence counter."""

        abstract = True
        ordering = ("sequence", "period")
        constraints = (
            models.UniqueConstraint(
                fields=("sequence", "period"),
                name="%(app_label)s_counter_sequence_period",
            ),
        )

    def __str__(self) -> str:
        """Return a readable label for Django displays."""

        return f"{self.sequence_id}:{self.period or '-'}={self.value}"


SequenceRole = role_anchor("sequence/role")
"""The ``sequence/role`` anchor: its const ``admin`` arm resolves a platform admin
as an effective sequence manager. See :func:`angee.base.models.role_anchor`.
"""
