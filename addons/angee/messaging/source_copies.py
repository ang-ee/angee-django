"""Actor-readable source identity over native, retained pair assertions.

PairAspect owns assertion state and historical replay. The only extra query
primitive is SQL reachability; it has no registry or materialized graph state.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Self

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import NotSupportedError, connections, models, router, transaction
from django.db.models.functions import Cast
from django.db.transaction import TransactionManagementError
from rebac import actor_context, current_actor, system_context, to_subject_ref

from angee.base.actors import instance_actor
from angee.base.changesets import changeset, changeset_model_label, current_changeset
from angee.base.evidence import EvidenceReference
from angee.base.mixins import PairAspect, StatementsMixin


class SourceIdentityConflict(ValidationError):
    """An observation conflicts with an applied keep-separate decision."""


class SourceCopyMixin(StatementsMixin, models.Model):
    """Identity evidence never moves the channel occurrence or its members."""

    same_as = PairAspect()

    class Meta:
        abstract = True

    def require_statement_write(self, actor: Any) -> None:
        self.require_access("assert_identity", actor)

    def lock_statement_subject(self, members: Mapping[str, Any], *, using: str) -> Any:
        """Serialize the source graph before taking native endpoint row locks.

        One transaction lock covers both Message and Thread: disjoint edges can
        connect through other rows, and ingest observes both kinds together.
        SQLite already serializes writers. Other database backends are explicit.
        """

        connection = connections[using]
        if not connection.in_atomic_block:
            raise TransactionManagementError("Source identity requires an atomic statement write.")
        if connection.vendor == "postgresql":
            with connection.cursor() as cursor:
                # Stable namespace 'ange' and concern 'msgi'; never row-derived.
                cursor.execute("SELECT pg_advisory_xact_lock(%s, %s)", (0x616E6765, 0x6D736769))
        elif connection.vendor != "sqlite":
            raise NotSupportedError("Source identity requires PostgreSQL or SQLite writer serialization.")
        return super().lock_statement_subject(members, using=using)

    def apply_pair(self, *, aspect: str, other: Any, statement: Any | None) -> None:
        """The resolved pair itself is the read rule; reversal needs no shadow state."""

        if aspect != "same_as":
            return super().apply_pair(aspect=aspect, other=other, statement=statement)
        # Reverting a decision uses this same hook. A preferred keep-separate
        # stance cannot silently lose to an alternate positive path.
        pairs = self.same_as.model
        using = self._state.db or router.db_for_write(type(self), instance=self)
        applied = pairs.statements.using(using).applied().filter(subject_id=models.OuterRef("pk"))
        active = pairs._base_manager.using(using).untrashed().filter(models.Exists(applied))
        rows = type(self)._base_manager.using(using).all()
        connected = rows.alias(_source_component=SourceRepresentative(
            models.F("pk"), pairs=active, visible=rows,
        )).filter(_source_component=SourceRepresentative(
            models.Value(self.pk), pairs=active, visible=rows,
        )).order_by().values("pk")
        declined = pairs.statements.using(using).current().filter(
            status="asserted", applied=True, snak="no_value", subject_id=models.OuterRef("pk"),
        )
        if pairs._base_manager.using(using).untrashed().filter(
            models.Exists(declined), left_id__in=connected, right_id__in=connected,
        ).exists():
            raise SourceIdentityConflict("Other identity evidence conflicts with keeping these copies separate. "
                                         "Withdraw the conflicting same-as assertions first.")

    def identify_source_copy(self, other: Any, *, same: bool) -> Any:
        """Record a person's reversible same/keep-separate decision for source rows."""

        if self.channel_id is None or other.channel_id is None or self.platform != other.platform:
            raise ValidationError("Source identity requires two occurrences on the same platform.")
        using = router.db_for_write(type(self), instance=self)
        actor = instance_actor(self) or current_actor()
        with changeset(activity="manual", origin=self), transaction.atomic(using=using):
            self.require_statement_write(actor)
            other.require_statement_write(actor)
            pair = self.same_as.for_pair(self, other)
            if same:
                return pair.confirm(aspect="same_as", values={})
            return pair.dismiss(aspect="same_as")


class SourceRepresentative(models.Func):
    """Smallest readable member of the applied same_as component.

    Django compiles both the endpoint expression and the already actor-scoped
    candidate subquery. Recursive UNION handles transitivity and terminates
    cycles. Each edge requires readable endpoints; hidden copies cannot connect visible rows.
    """

    output_field = models.BigIntegerField()

    def __init__(self, key: Any, *, pairs: models.QuerySet, visible: models.QuerySet) -> None:
        self.pair_table = pairs.model._meta.db_table
        super().__init__(
            Cast(key, output_field=models.BigIntegerField()),
            models.Subquery(pairs.order_by().values("pk")),
            models.Subquery(visible.order_by().values("pk")),
        )

    def as_sql(self, compiler: Any, connection: Any, **extra_context: Any) -> tuple[str, list[Any]]:
        key, pairs, visible = [compiler.compile(expression) for expression in self.source_expressions]
        table = connection.ops.quote_name(self.pair_table)
        return (
            "(WITH RECURSIVE source_copies(id) AS ("
            f"SELECT {key[0]} UNION SELECT CASE WHEN edge.left_id = source_copies.id "
            f"THEN edge.right_id ELSE edge.left_id END FROM {table} edge "
            "JOIN source_copies ON edge.left_id = source_copies.id OR edge.right_id = source_copies.id "
            f"WHERE edge.id IN {pairs[0]}) "
            f"SELECT MIN(id) FROM source_copies WHERE id IN {visible[0]})",
            [*key[1], *pairs[1], *visible[1]],
        )


class SourceCopyQuerySet(models.QuerySet):
    """Choose display rows after the caller's actor, source and search filters."""

    def identity_pairs(self) -> models.QuerySet:
        pairs = self.model.same_as.model
        applied = pairs.statements.using(self.db).applied().filter(subject_id=models.OuterRef("pk"))
        actor = self.actor() or current_actor()
        readable = self.model._default_manager.using(self.db).all()
        if actor is not None:
            readable = readable.with_actor(actor)
        readable = readable.scoped().order_by().values("pk")
        return pairs._base_manager.using(self.db).untrashed().filter(
            models.Exists(applied), left_id__in=readable, right_id__in=readable,
        )

    def representatives(self) -> Self:
        """Every readable row without identity evidence, plus one representative per component.

        The recursive walk is correlated per row, so only rows that are an endpoint
        of an applied pair pay for it; the rest pass on one anti-join.
        """

        visible = self.scoped()
        pairs = self.identity_pairs()
        paired = pairs.filter(models.Q(left_id=models.OuterRef("pk")) | models.Q(right_id=models.OuterRef("pk")))
        return visible.alias(_source_representative=SourceRepresentative(
            models.F("pk"), pairs=pairs, visible=visible,
        )).filter(~models.Exists(paired) | models.Q(pk=models.F("_source_representative")))

    def source_copies(self, record: models.Model) -> Self:
        """Only copies readable to this caller, including the supplied occurrence."""

        record.require_access("read", self.actor() or current_actor())
        visible = self.scoped()
        representative = SourceRepresentative(
            models.Value(record.pk), pairs=self.identity_pairs(), visible=visible,
        )
        return visible.alias(_source_representative=SourceRepresentative(
            models.F("pk"), pairs=self.identity_pairs(), visible=visible,
        )).filter(_source_representative=representative)


def observe_email_identity(message_id: Any, *, using: str = "default") -> None:
    """Reconcile one committed RFC Message-ID across channel occurrences.

    Chat/provider IDs have account-local meaning and are never inferred here.
    This callback takes native pair locks after the envelope transaction commits,
    so two channel ingesters do not hold opposing message locks.
    """

    message_model = apps.get_model("messaging", "Message")
    rule_model = apps.get_model("provenance", "Rule")
    with system_context(reason="messaging.source_identity.rule"):
        try:
            source = rule_model.objects.db_manager(using).for_key("messaging.source_identity")
        except rule_model.DoesNotExist:
            # The envelope has landed; identity waits for the install resources
            # (the rule row and its grants), whose load backfills every copy.
            return
    actor = to_subject_ref(source)
    with actor_context(actor), changeset(
        fresh=True, activity="system", change_reason="messaging.source_identity", origin=source,
    ):
        message = message_model.objects.using(using).with_actor(actor).get(pk=message_id)
        if message.message_type != "email" or not message.external_id or message.channel_id is None:
            return
        copies = message_model.objects.using(using).with_actor(actor).with_external_ids((message.external_id,)).filter(
            platform=message.platform, message_type="email", channel__isnull=False,
        ).exclude(channel_id=message.channel_id).select_related("thread").order_by("pk")
        for copy in copies:
            evidence = [EvidenceReference.of(row) for row in sorted((message, copy), key=lambda row: row.pk)]
            for left, right in ((message, copy), (message.thread, copy.thread)):
                if left is None or right is None or left.pk == right.pk:
                    continue
                try:
                    with transaction.atomic(using=using):
                        pair = type(left).same_as.for_pair(left.with_actor(actor), right)
                        pair.statements.set(aspect="same_as", values={}, asserted_by=source, evidence=evidence)
                except SourceIdentityConflict:
                    # The committed envelope remains useful. Roll back this
                    # observation (and any new carrier), preserving the person's
                    # applied separation and every physical source occurrence.
                    continue


def backfill_email_identity(source: Any) -> None:
    """Observe existing email copies after install resources and grants load.

    The successful changeset is the upgrade mark. A failed load rolls back its
    assertions and mark together; ordinary ingestion uses the same observation
    owner after each committed envelope.
    """

    using = source._state.db or "default"
    reason = "messaging.source_identity.backfill"
    storage_label = changeset_model_label()
    if not storage_label:
        raise ImproperlyConfigured("Messaging source identity requires ANGEE_CHANGESET_MODEL.")
    history = apps.get_model(storage_label)._base_manager.using(using)
    if history.filter(change_reason=reason, outcome="success").exists():
        return
    with changeset(fresh=True, activity="system", change_reason=reason, origin=source), transaction.atomic(using=using):
        messages = apps.get_model("messaging", "Message")._base_manager.using(using).filter(
            message_type="email", channel__isnull=False,
        ).exclude(external_id="").order_by("pk")
        for message_id in messages.values_list("pk", flat=True).iterator():
            observe_email_identity(message_id, using=using)
        current_changeset(required=True, record=True)
