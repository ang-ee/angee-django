"""Changeset storage scopes and atomic creation claims."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any, Self

from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.db.models import Q
from django_provenance.querysets import ChangesetQuerySet as NativeChangesetQuerySet
from simple_history.utils import get_history_manager_for_model

from angee.base.querysets import AngeeQuerySet
from angee.provenance.contrib.angee.history import HistoryMixin
from angee.provenance.querysets import model_history
from angee.provenance.statements import StatementsMixin


class ChangesetQuerySet[T: models.Model](AngeeQuerySet[T], NativeChangesetQuerySet):
    """Changeset scopes derived from the history contracts of records they touch."""

    def touching(self, record: Any) -> Self:
        """Return changesets belonging to ``record``'s declared history document."""

        return super().touching(record) | self.filter(self._merge_touching(record))

    @staticmethod
    def _merge_touching(record: HistoryMixin) -> Q:
        predicate = Q(pk__in=[])
        content_types = ContentType.objects.db_manager(hints={"instance": record}).get_for_models(
            *record.history_tables(),
        )
        for table, content_type in content_types.items():
            predicate |= Q(
                activity="merge", origin_content_type=content_type,
                origin_object_id=str(record._get_pk_val(table._meta)),
            )
        return predicate

    def records_touched(self) -> Iterable[tuple[type[models.Model], Any]]:
        """Yield only identities of existing records whose history documents we touch.

        This is the inverse of ``touching``: the same history scopes cover MTI,
        owned lines, pairs and their statements, plus an unchanged merge survivor.
        SQL selects identities, never live/history bodies. The touched-model index
        avoids probing documents whose tables cannot intersect these boundaries.
        """

        merges: dict[int, set[int]] = {}
        for content_type_id, pk in self.filter(activity="merge").values_list(
            "origin_content_type_id", "origin_object_id"
        ):
            if content_type_id is not None and pk is not None:
                merges.setdefault(content_type_id, set()).add(int(pk))

        def additional(
            record: HistoryMixin, touched_ids: Callable[[type[models.Model], str], set[Any]]
        ) -> Iterable[Any]:
            ids: set[Any] = set()
            tables = record.history_tables()

            def include(table: type[models.Model], column: str, keys: Iterable[Any]) -> None:
                field = record._history_identity_field(table, column)
                ids.update(
                    type(record)._base_manager.using(self.db)
                    .filter(**{f"{field.name}__in": keys}).values_list("pk", flat=True)
                )
            for table, columns in record.history_identity_columns().items():
                if not issubclass(table, StatementsMixin):
                    continue
                for statement in table.statement_models():
                    subjects = touched_ids(statement, "subject_id")
                    if table in tables:
                        include(statement, "subject_id", subjects)
                    elif subjects:
                        history = (
                            get_history_manager_for_model(table)
                            .using(self.db)
                            .filter(**{f"{table._meta.pk.attname}__in": subjects})
                        )
                        for column in columns:
                            include(table, column, history.order_by().values_list(column, flat=True))
            if merges:
                content_types = ContentType.objects.db_manager(self.db).get_for_models(*tables)
                for table, content_type in content_types.items():
                    include(table, table._meta.pk.attname, merges.get(content_type.pk, ()))
            return ids

        documents = (
            model for model in apps.get_models() if issubclass(model, HistoryMixin) and model_history(model) is not None
        )
        yield from self._records_touched(documents, additional)
