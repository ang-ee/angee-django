"""Relationship analytics derived from deliberate party-to-party interactions.

``Tie`` is fully recomputable from messaging: directed message recipients,
replies, and mentions become one canonical party pair. Thread-roster
co-membership is deliberately not evidence of a tie — a message sent into a
100-member group is not 99 relationships. Record chatter and public-post threads
are outside this analytics surface.

Gravity is ``log2(message_count + 1) × recency × reciprocity × diversity``.
Recency decays on a 30-day scale, reciprocity is the smaller directional count
divided by the larger, and every platform after the first adds ten percent.
Fading begins when silence exceeds the greater of sixty days or eight times the
edge's average interaction interval; fewer than two interactions establish no
rhythm and never fade.

``Cadence`` is the addon's sole human-authored fact. Its due date derives from a
user's cadence and the last interaction on that user's party edge.
"""

from __future__ import annotations

import datetime
import math
from collections.abc import Mapping, Sequence
from typing import Any

from django.apps import apps
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from rebac import current_actor

from angee.base.actors import actor_user_id, is_user_actor
from angee.base.mixins import SqidMixin
from angee.base.models import AngeeModel
from angee.nexus.managers import CadenceManager, TieManager


class Tie(SqidMixin, AngeeModel):
    """A fully derived interaction edge between two canonically ordered parties."""

    runtime = True
    sqid_prefix = "tie_"

    party_a = models.ForeignKey(
        "parties.Party",
        on_delete=models.CASCADE,
        related_name="ties_as_a",
    )
    party_b = models.ForeignKey(
        "parties.Party",
        on_delete=models.CASCADE,
        related_name="ties_as_b",
    )
    a_to_b_count = models.PositiveIntegerField(default=0)
    b_to_a_count = models.PositiveIntegerField(default=0)
    message_count = models.PositiveIntegerField(default=0)
    thread_count = models.PositiveIntegerField(default=0)
    platforms = models.JSONField(blank=True, default=list)
    first_interaction_at = models.DateTimeField(null=True, blank=True)
    last_interaction_at = models.DateTimeField(null=True, blank=True, db_index=True)
    gravity = models.FloatField(default=0.0, db_index=True)
    is_fading = models.BooleanField(default=False, db_index=True)

    objects = TieManager()

    class Meta:
        """Django model options for the derived pair edge."""

        abstract = True
        ordering = ("-gravity", "sqid")
        rebac_resource_type = "nexus/tie"
        rebac_id_attr = "sqid"
        constraints = (
            models.CheckConstraint(
                condition=models.Q(party_a__lt=models.F("party_b")),
                name="ck_tie_party_order",
            ),
            models.UniqueConstraint(
                fields=("party_a", "party_b"),
                name="uq_tie_party_pair",
            ),
        )

    def __str__(self) -> str:
        """Return the canonical pair and current gravity for Django displays."""

        party_a_id = getattr(self, "party_a_id", None)
        party_b_id = getattr(self, "party_b_id", None)
        return f"tie:{party_a_id}<->{party_b_id} g={self.gravity:.2f}"

    @staticmethod
    def compute_gravity(
        *,
        message_count: int,
        a_to_b_count: int,
        b_to_a_count: int,
        last_at: datetime.datetime | None,
        platform_count: int,
        now: datetime.datetime,
    ) -> float:
        """Return volume × recency × reciprocity × platform diversity."""

        if not message_count or last_at is None:
            return 0.0
        volume = math.log2(message_count + 1)
        days_since = max((now - last_at).total_seconds() / 86400.0, 0.0)
        recency = 1.0 / (1.0 + days_since / 30.0)
        top = max(a_to_b_count, b_to_a_count)
        reciprocity = (min(a_to_b_count, b_to_a_count) / top) if top else 0.0
        diversity = 1.0 + 0.1 * max(platform_count - 1, 0)
        return volume * recency * reciprocity * diversity

    @staticmethod
    def check_fading(
        *,
        message_count: int,
        first_at: datetime.datetime | None,
        last_at: datetime.datetime | None,
        now: datetime.datetime,
    ) -> bool:
        """Return whether silence exceeds max(8 × average interval, 60 days)."""

        if message_count < 2 or first_at is None or last_at is None:
            return False
        span_days = (last_at - first_at).total_seconds() / 86400.0
        avg_interval = span_days / max(message_count - 1, 1)
        threshold = max(8.0 * avg_interval, 60.0)
        gap_days = (now - last_at).total_seconds() / 86400.0
        return gap_days > threshold

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist the pair in canonical primary-key order."""

        party_a_id = getattr(self, "party_a_id", None)
        party_b_id = getattr(self, "party_b_id", None)
        if party_a_id is not None and party_b_id is not None and party_a_id > party_b_id:
            party_a = self._state.fields_cache.pop("party_a", None)
            party_b = self._state.fields_cache.pop("party_b", None)
            setattr(self, "party_a_id", party_b_id)
            setattr(self, "party_b_id", party_a_id)
            if party_b is not None:
                self.party_a = party_b
            if party_a is not None:
                self.party_b = party_a
            update_fields = kwargs.get("update_fields")
            if update_fields is not None:
                kwargs["update_fields"] = {*update_fields, "party_a", "party_b"}
        super().save(*args, **kwargs)


class Cadence(SqidMixin, AngeeModel):
    """One user's human-authored stay-in-touch intent for one party."""

    runtime = True
    sqid_prefix = "cad_"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="nexus_cadences",
    )
    party = models.ForeignKey(
        "parties.Party",
        on_delete=models.CASCADE,
        related_name="cadences",
    )
    cadence_days = models.PositiveIntegerField()
    touch_due_at = models.DateTimeField(null=True, blank=True, db_index=True, editable=False)

    objects = CadenceManager()

    class Meta:
        """Django model options for the user-party cadence."""

        abstract = True
        ordering = ("touch_due_at", "sqid")
        rebac_resource_type = "nexus/cadence"
        rebac_id_attr = "sqid"
        constraints = (
            models.UniqueConstraint(
                fields=("user", "party"),
                name="uq_cadence_user_party",
            ),
        )

    def __str__(self) -> str:
        """Return the cadence's user-party key for Django displays."""

        return f"cadence:{self.user_id}:{self.party_id}"

    def derive_touch_due(self) -> datetime.datetime | None:
        """Derive the next due date from the viewer's party edge."""

        if self.user_id is None or self.party_id is None:
            return None
        party_model = apps.get_model("parties", "Party")
        viewer = party_model.objects.identity_for_user_id(self.user_id)
        if viewer is None or viewer.pk == self.party_id:
            return None
        tie_model = apps.get_model("nexus", "Tie")
        party_a_id, party_b_id = sorted((viewer.pk, self.party_id))
        last_at = (
            tie_model._base_manager.filter(party_a_id=party_a_id, party_b_id=party_b_id)
            .values_list("last_interaction_at", flat=True)
            .first()
        )
        if last_at is None:
            return None
        return last_at + datetime.timedelta(days=self.cadence_days)

    def refresh_touch_due(self) -> None:
        """Recompute and persist the server-owned due date."""

        touch_due_at = self.derive_touch_due()
        if self.touch_due_at == touch_due_at:
            return
        self.touch_due_at = touch_due_at
        super().save(update_fields=["touch_due_at", "updated_at"])

    def apply_create_defaults(self) -> Mapping[str, Sequence[Any]]:
        """Bind a blank user relation to the authenticated REBAC actor."""

        contributions = dict(super().apply_create_defaults())
        if self.user_id is not None:
            return contributions
        actor = current_actor()
        if not is_user_actor(actor):
            raise ValidationError({"user": "An authenticated user is required."})
        user_id = actor_user_id(actor)
        user_model = type(self)._meta.get_field("user").related_model
        user = user_model._base_manager.filter(pk=user_id).first()
        if user is None:
            raise ValidationError({"user": "The authenticated user no longer exists."})
        self.user = user
        contributions["user"] = (user,)
        return contributions

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Refresh the server-owned due date whenever cadence intent changes."""

        self.touch_due_at = self.derive_touch_due()
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            kwargs["update_fields"] = {*update_fields, "touch_due_at"}
        super().save(*args, **kwargs)
