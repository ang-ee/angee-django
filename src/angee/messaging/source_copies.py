"""Channel copies of one message or conversation, folded into a retained redirect.

A source thread or message is one channel's occurrence. When evidence says two
occurrences are the same (an RFC Message-ID received through two accounts, or a
person's decision), the pair's ``same_as`` statement is the fact and
:class:`~angee.iam.models.merge.ProtectedMergeableMixin` the mechanism: applying the statement
folds the newer copy into the older through ``merged_into`` and nothing else
moves, so reply transport, delivery, cursors and purge keep addressing the
physical row. Readers collapse copies through
:meth:`~angee.base.querysets.MergeableQuerySet.canonical`, which still shows a
copy whose survivor the reader cannot see. Message feeds additionally retain an
explicitly addressed occurrence under their current read and search scope.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from django.apps import apps
from django.core.exceptions import ValidationError
from rebac import system_context, to_subject_ref

from angee.iam.models.merge import ProtectedMergeableMixin
from angee.parties.models import Handle
from angee.provenance.context import changeset
from angee.provenance.evidence import EvidenceReference
from angee.provenance.statements import (
    PairAspect,
    StatementsMixin,
)

SOURCE_IDENTITY_RULE = "messaging.source_identity"
"""The provenance rule that asserts email copies, and the reason its folds record."""


class SourceCopyMixin(ProtectedMergeableMixin, StatementsMixin):
    """A channel occurrence; an applied ``same_as`` statement folds it into its oldest copy."""

    channel_id: int | None
    platform: Handle.Platform

    same_as = PairAspect()
    retains_merged = True
    merge_moves_references = False

    class Meta:
        abstract = True

    def validate_merge(self, merged: Sequence[Any]) -> None:
        super().validate_merge(merged)
        for row in merged:
            if self.same_as.kept_separate(self, row):
                raise ValidationError("These copies have been marked to stay separate.")

    def retire_merged(self, merged: Any) -> None:
        """A copy stays whole and untrashed: a reader without the survivor still reads it."""

        merged.merged_into = self
        merged.save(using=merged._state.db, update_fields=("merged_into",))

    def apply_pair(self, *, aspect: str, other: Any, statement: Any | None) -> None:
        """Derive the redirect from the pair's applied stance; reverting the statement reverts it."""

        if aspect != "same_as":
            return super().apply_pair(aspect=aspect, other=other, statement=statement)
        same = statement is not None and statement.snak == statement.StatementSnak.VALUE
        with system_context(reason=SOURCE_IDENTITY_RULE):
            survivor, copy = sorted((self.canonical(), other.canonical()), key=lambda row: row.pk)
            if same and survivor.pk != copy.pk:
                survivor.fold([copy])
            elif not same and survivor.pk == copy.pk:
                # Kept separate: the newer redirect leaves the survivor's group.
                redirected = [row for row in (self, other) if row.merged_into_id is not None]
                if redirected:
                    row = max(redirected, key=lambda row: row.pk)
                    row.merged_into = None
                    row.save(update_fields=("merged_into",))
        return None

    def identify_source_copy(self, other: Any, *, same: bool) -> Any:
        """Record a person's reversible same/keep-separate decision for two occurrences."""

        if self.channel_id is None or other.channel_id is None or self.platform != other.platform:
            raise ValidationError("Source identity requires two occurrences on the same platform.")
        pair = self.same_as.for_pair(self, other)
        return pair.confirm(aspect="same_as", values={}) if same else pair.dismiss(aspect="same_as")


def observe_email_identity(messages: Sequence[Any]) -> None:
    """Assert cross-channel email identity once for a committed ingest batch.

    An RFC Message-ID names one email wherever it is received; chat and provider
    ids are account-local and never compared. The rule asserts the message pair
    and the thread pair; applying each statement folds the copy into its oldest
    occurrence unless a person keeps them separate.
    """

    if not messages:
        return
    message_model = apps.get_model("messaging", "Message")
    using = messages[0]._state.db
    keys = {message.pk for message in messages if message.message_type == "email"}
    if not keys:
        return
    rule_model = apps.get_model("provenance", "Rule")
    with system_context(reason=SOURCE_IDENTITY_RULE):
        try:
            source = rule_model.objects.db_manager(using).get(key=SOURCE_IDENTITY_RULE)
        except rule_model.DoesNotExist:
            # The envelope landed before the install resources (the rule and its grants).
            return
        landed = list(message_model._base_manager.using(using).filter(pk__in=keys).select_related("thread"))
    landed = [row for row in landed if row.external_id and row.channel_id is not None]
    if not landed:
        return
    actor = to_subject_ref(source)
    candidates: dict[tuple[str, str], list[Any]] = {}
    for platform in sorted({row.platform for row in landed}):
        external_ids = {row.external_id for row in landed if row.platform == platform}
        copies = (
            message_model.objects.db_manager(using).with_actor(actor)
            .with_external_ids(sorted(external_ids))
            .filter(platform=platform, message_type="email", channel__isnull=False)
            .select_related("thread")
            .order_by("pk")
        )
        for copy in copies:
            candidates.setdefault((copy.platform, copy.external_id), []).append(copy)
    pairs = []
    seen = set()
    for message in landed:
        for copy in candidates.get((message.platform, message.external_id), ()):
            key = tuple(sorted((message.pk, copy.pk)))
            if message.channel_id != copy.channel_id and key not in seen:
                seen.add(key)
                pairs.append((message, copy))
    if not pairs:
        return
    with changeset(fresh=True, activity="system", change_reason=SOURCE_IDENTITY_RULE, origin=source):
        for message, copy in pairs:
            evidence = [EvidenceReference.of(row) for row in sorted((message, copy), key=lambda row: row.pk)]
            for left, right in ((message, copy), (message.thread, copy.thread)):
                if left is None or right is None or left.pk == right.pk:
                    continue
                pair = type(left).same_as.for_pair(left.with_actor(actor), right)
                pair.statements.set(aspect="same_as", values={}, asserted_by=source, evidence=evidence)
