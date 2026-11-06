"""Native relation fields with database-bound content types and collection policy."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from django.contrib.contenttypes.fields import GenericRelation as DjangoGenericRelation
from django.contrib.contenttypes.models import ContentType
from django.db import DEFAULT_DB_ALIAS, models
from django.db.models.sql.where import WhereNode

if TYPE_CHECKING:
    from django.db.backends.base.base import BaseDatabaseWrapper
    from django.db.models.sql.compiler import SQLCompiler, _AsSqlType


class _ContentTypePrimaryKey(models.Expression):
    """Resolve a generic relation's content type when its SQL alias is known."""

    def __init__(self, model: type[models.Model], for_concrete_model: bool, output_field: models.Field) -> None:
        super().__init__(output_field=output_field)
        self.model = model
        self.for_concrete_model = for_concrete_model

    def as_sql(self, compiler: SQLCompiler, connection: BaseDatabaseWrapper) -> _AsSqlType:
        # The executing query's alias wins over the ContentType read router. The
        # native manager creates a missing row on that alias, as for a normal GFK.
        content_type = ContentType.objects.db_manager(connection.alias).get_for_model(
            self.model, for_concrete_model=self.for_concrete_model,
        )
        return compiler.compile(models.Value(content_type.pk, output_field=self.output_field))


class GenericRelation(DjangoGenericRelation):
    """Bind reverse joins to the compiling alias, creating its ContentType row if absent.

    The explicit alias bypasses ContentType's read router, as the query itself
    has already selected its database.
    """

    def get_extra_restriction(self, alias: str | None, remote_alias: str) -> WhereNode:
        field = self.remote_field.model._meta.get_field(self.content_type_field_name)
        assert isinstance(field, models.ForeignKey)
        lookup_type = field.get_lookup("exact")
        assert lookup_type is not None
        lookup = lookup_type(
            field.get_col(remote_alias),
            _ContentTypePrimaryKey(self.model, self.for_concrete_model, field.target_field),
        )
        return WhereNode([lookup])  # type: ignore[list-item]  # django-stubs omits Lookup from WhereNode children.


class RetainedGenericRelation(GenericRelation):
    """Query retained edges without collecting them on the target's deletion."""

    def bulk_related_objects(
        self, objs: Sequence[models.Model], using: str = DEFAULT_DB_ALIAS,
    ) -> models.QuerySet[models.Model]:
        return models.QuerySet(model=self.related_model, using=using).none()
