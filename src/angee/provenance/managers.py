"""Changeset storage scopes and atomic creation claims."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Self, cast

from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, models, transaction
from django.db.models import Q
from simple_history.utils import get_history_manager_for_model

from angee.base.managers import AngeeManager
from angee.base.querysets import AngeeQuerySet
from angee.provenance.context import current_changeset, discard_changeset_claim, require_changeset
from angee.provenance.contrib.angee.exceptions import CreationKeyConflict
from angee.provenance.contrib.angee.history import HistoryMixin
from angee.provenance.contrib.angee.serialization import creation_fingerprint
from angee.provenance.querysets import model_history
from angee.provenance.statements import StatementsMixin

if TYPE_CHECKING:
    from angee.provenance.models import Changeset


class ChangesetQuerySet[T: models.Model](AngeeQuerySet[T]):
    """Changeset scopes derived from the history contracts of records they touch."""

    def touching(self, record: HistoryMixin) -> Self:
        """Return changesets belonging to ``record``'s declared history document."""

        return self.filter(self.touching_filter(record))

    @staticmethod
    def _history_rows(scopes: Mapping[type[models.Model], Q]) -> Iterator[models.QuerySet[Any]]:
        for table, predicate in scopes.items():
            yield get_history_manager_for_model(table).filter(predicate).order_by()

    @staticmethod
    def touching_filter(record: HistoryMixin) -> Q:
        """Structural document membership, shared by history reads and existence probes."""

        rows = [
            history.filter(changeset_id__isnull=False).values_list("changeset_id", flat=True)
            for history in ChangesetQuerySet._history_rows(record.history_scopes())
        ]
        membership = Q(pk__in=models.Subquery(rows[0].union(*rows[1:]))) if rows else Q(pk__in=())
        return membership | ChangesetQuerySet._merge_touching(record)

    def history_written_only_by(self, record: models.Model, predicate: Q) -> bool:
        """Whether a created history document was written only by matching changesets.

        Compose the record's ``history_scope`` (concrete tables and owned lines),
        using the same history-row owner as ``touching_filter``. At least one
        creation snapshot is required; any row with a missing changeset or one
        outside this queryset and predicate refuses. Statement authorship is a
        separate live-reference contract, not part of this document predicate.
        A record without history refuses.
        """

        if not isinstance(record, HistoryMixin):
            return False
        matching = self.filter(predicate).order_by().values("pk")
        created = False
        for history in self._history_rows(record.history_scope()):
            if history.exclude(changeset_id__in=matching).exists():
                return False
            created = created or history.filter(history_type="+").exists()
        return created

    def closed_for_record(self, record: HistoryMixin) -> Self:
        """Return the closed changesets touching an admitted record's document.

        History and the timeline share this rule: a record's read implies only its
        closed changesets; an open one stays behind the changeset's own read.
        """

        return self.filter(self.closed_for_record_filter(record))

    def closed_for_record_filter(self, record: HistoryMixin) -> Q:
        """Closed document membership for both authorized reads and structural probes."""

        return self.touching_filter(record) & Q(state=cast(Any, self.model).State.CLOSED)

    @staticmethod
    def _merge_touching(record: HistoryMixin) -> Q:
        content_types = [ContentType.objects.get_for_model(table) for table in record.history_tables()]
        return Q(
            activity="merge",
            origin_content_type__in=content_types,
            origin_object_id=str(record.pk),
        )

    def records_touched(self) -> Iterable[tuple[type[models.Model], Any]]:
        """Yield only identities of existing records whose history documents we touch.

        This is the inverse of ``touching``: the same history scopes cover MTI,
        owned lines, pairs and their statements, plus an unchanged merge survivor.
        SQL selects identities, never live/history bodies. Only the documents of
        the touched-model index and the merge origins are probed, so the cost
        follows the tables these boundaries touched, not the installed models.
        """

        touched = {label for labels in self.order_by().values_list("touched", flat=True) for label in labels}
        boundaries = self.order_by().values("pk")
        merges: dict[int, set[int]] = {}
        for content_type_id, pk in self.filter(activity="merge").values_list(
            "origin_content_type_id",
            "origin_object_id",
        ):
            if content_type_id is not None and pk is not None:
                merges.setdefault(content_type_id, set()).add(int(pk))
        origins = (ContentType.objects.get_for_id(content_type_id).model_class() for content_type_id in merges)
        labels = touched | {origin._meta.label_lower for origin in origins if origin is not None}
        documents = _history_documents(tuple(apps.get_models()))
        identities: dict[tuple[type[models.Model], str], set[Any]] = {}

        def touched_ids(table: type[models.Model], column: str) -> set[Any]:
            key = table, column
            if key not in identities:
                identities[key] = set(
                    get_history_manager_for_model(table)
                    .filter(changeset_id__in=boundaries)
                    .order_by()
                    .values_list(column, flat=True)
                )
            return identities[key]

        candidates = {model for label in labels for model in documents.get(label, ())}
        for model in sorted(candidates, key=lambda model: model._meta.label_lower):
            record = model()
            ids: set[Any] = set()
            for table, columns in record.history_identity_columns().items():
                if table._meta.label_lower in touched:
                    for column in columns:
                        ids.update(touched_ids(table, column))
                for statement in table.statement_models() if issubclass(table, StatementsMixin) else ():
                    if statement._meta.label_lower not in touched:
                        continue
                    subjects = touched_ids(statement, "subject_id")
                    if table in record.history_tables():
                        ids.update(subjects)
                    elif subjects:
                        # Deleted/moved owned rows still identify every document
                        # their statements belong to through retained snapshots.
                        history = get_history_manager_for_model(table).filter(
                            **{f"{table._meta.pk.attname}__in": subjects},
                        )
                        for column in columns:
                            ids.update(history.order_by().values_list(column, flat=True))
            if merges:
                content_types = ContentType.objects.get_for_models(*record.history_tables())
                for content_type in content_types.values():
                    ids.update(merges.get(content_type.pk, ()))
            rows = model._base_manager.filter(pk__in=ids)
            yield from ((model, pk) for pk in rows.order_by("pk").values_list("pk", flat=True))


@lru_cache(maxsize=1)
def _history_documents(installed: tuple[type[models.Model], ...]) -> Mapping[str, set[type[HistoryMixin]]]:
    """Index each history and statement table label by the installed records whose documents hold it.

    The inverse of ``history_identity_columns`` and ``statement_models``, computed
    once per app registry (``installed`` is its model list).
    """

    documents: defaultdict[str, set[type[HistoryMixin]]] = defaultdict(set)
    for model in installed:
        if issubclass(model, HistoryMixin) and model_history(model) is not None:
            for table in model().history_identity_columns():
                statements = table.statement_models() if issubclass(table, StatementsMixin) else ()
                for owner in (table, *statements):
                    documents[owner._meta.label_lower].add(model)
    return documents


_ChangesetManagerBase = AngeeManager.from_queryset(ChangesetQuerySet)


class ChangesetManager[ChangesetT: Changeset](_ChangesetManagerBase[ChangesetT]):
    """Atomic idempotency claims on logical write boundaries."""

    def claim(self, scope: str, key: str, *, fingerprint: str) -> ChangesetT | None:
        """Claim ``(scope, key)`` on the fresh current boundary or return its completed match."""

        for name, value in (("scope", scope), ("key", key), ("fingerprint", fingerprint)):
            error_name = "idempotency_key" if name == "key" else name
            try:
                field = self.model._meta.get_field(name)
                assert isinstance(field, models.Field)
                field.clean(value, None)
            except ValidationError as error:
                raise ValidationError({error_name: error}) from error
            if not value.strip():
                raise ValidationError({error_name: f"A changeset {name} must not be blank."})
        if not connection.in_atomic_block:
            raise RuntimeError("Changeset claims require an outer transaction.atomic() boundary.")
        claimed = require_changeset()
        if not isinstance(claimed, self.model):
            raise RuntimeError("Claims must use the configured changeset model.")
        if not claimed._state.adding or claimed.pk is not None:
            raise RuntimeError("Claim an idempotency key before the boundary records any writes.")
        claimed.scope = scope
        claimed.key = key
        claimed.fingerprint = fingerprint
        try:
            with transaction.atomic():
                current_changeset(required=True, record=True)
        except IntegrityError:
            discard_changeset_claim(claimed)
            existing = self.model._base_manager.filter(scope=scope, key=key).first()
            if existing is None:
                raise
            if existing.fingerprint != fingerprint:
                raise CreationKeyConflict("This idempotency key was already used for different content.")
            return existing
        return None

    def claim_created[CreatedT: models.Model](
        self,
        scope: str,
        key: str,
        *,
        values: Any,
        model: type[CreatedT],
        unavailable: Exception,
    ) -> CreatedT | None:
        """Claim creation content or return its typed result, before domain replay policy.

        The caller still owns its transaction, authorization and every refusal
        specific to the returned row. The storage owner alone fingerprints the
        creation values and validates the receipt's result identity.
        """
        replay = self.claim(scope, key, fingerprint=creation_fingerprint(values))
        if replay is None:
            return None
        created = replay.created_row
        if not isinstance(created, model):
            raise unavailable
        return created
