"""Shared parties primitives: the confidence-bearing, human-reviewable link.

A :class:`ScoredLinkMixin` is a proposed edge that a sync, a rule, or a human can
suggest weakly and a human can later confirm or dismiss — the shape shared by
:class:`~angee.parties.models.PartyHandle` (a party↔handle identity claim),
:class:`~angee.parties.models.CircleMember` (a party's circle membership), and the
spaces addon's roster membership. Extracting it once means one :class:`LinkSource`
enum instead of a per-model copy, so the GraphQL enum name no longer collides.
"""

from __future__ import annotations

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from angee.base.fields import StateField


class LinkSource(models.TextChoices):
    """Where a scored link came from — the one provenance vocabulary for every link.

    A single enum shared by every :class:`ScoredLinkMixin` consumer: the class name
    projects as a global GraphQL enum name, so one class keeps that name unique
    instead of forcing a distinct per-model enum.
    """

    MANUAL = "manual", "Manual"
    IMPORT = "import", "Import"
    EMAIL_MATCH = "email_match", "Email Match"
    LLM = "llm", "LLM"
    OAUTH = "oauth", "OAuth"
    CARDDAV = "carddav", "CardDAV"
    COMMUNITY = "community", "Community"
    RULE = "rule", "Rule"


class ScoredLinkMixin(models.Model):
    """A confidence-bearing link between two rows, reviewable by a human.

    Carries the score (``confidence`` in ``0..1``), its ``source``, and the two
    review flags (``is_confirmed`` / ``is_dismissed``). :meth:`confirm` and
    :meth:`dismiss` are plain flag-flips followed by a re-resolve hook — they carry
    **no** REBAC awareness. A subclass whose confirm/dismiss must gate the actor or
    cascade a derived pointer overrides them and calls ``super()`` (see
    :class:`~angee.parties.models.PartyHandle`). Consumers also compose
    :class:`~angee.base.mixins.AuditMixin`; the transition saves include its
    ``updated_at`` field.
    """

    confidence = models.FloatField(
        default=1.0,
        validators=(MinValueValidator(0.0), MaxValueValidator(1.0)),
    )
    """How strong the link is, ``0`` (weak guess) to ``1`` (certain)."""

    source = StateField(choices_enum=LinkSource, default=LinkSource.MANUAL)
    """Where the link came from (a sync, a rule, an email match, a human)."""

    is_confirmed = models.BooleanField(default=False)
    """Whether a human accepted the link — the strongest resolution signal."""

    is_dismissed = models.BooleanField(default=False)
    """Whether a human rejected the link — the durable anti-link that survives re-suggestion."""

    class Meta:
        """Django model options for scored-link abstract inheritance."""

        abstract = True

    def confirm(self) -> None:
        """Accept this link at full confidence, then re-resolve any derived owner.

        Confirmation is the strongest signal, so the link also takes full confidence
        and the ``manual`` source — a later sync must not out-score a human decision.
        """

        self.confidence = 1.0
        self.source = LinkSource.MANUAL  # type: ignore[assignment]  # TextChoices member unmodeled without django-stubs
        self.is_confirmed = True
        self.is_dismissed = False
        self.save(update_fields=["confidence", "source", "is_confirmed", "is_dismissed", "updated_at"])
        self._resolve_link()

    def dismiss(self) -> None:
        """Reject this link — the durable anti-link — then re-resolve any derived owner.

        A dismissed link survives as a row so the same match is never re-proposed
        (suggesters key on the pair and skip an existing link); resolution ignores it.
        """

        self.is_dismissed = True
        self.is_confirmed = False
        self.save(update_fields=["is_dismissed", "is_confirmed", "updated_at"])
        self._resolve_link()

    def _resolve_link(self) -> None:
        """Re-derive any owner pointer this link feeds, after a review decision.

        The default is a no-op — a link whose confirm/dismiss materialises a derived
        pointer (``PartyHandle`` → ``Handle.party``) overrides this to re-run it.
        """

        _ = self
