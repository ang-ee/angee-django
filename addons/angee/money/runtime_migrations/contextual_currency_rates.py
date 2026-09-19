"""Add exact-context rates while preserving legacy configured-reference rows."""

from __future__ import annotations

from typing import Any, ClassVar

import django.db.models.deletion
from angee.base.fields import SqidField
from angee.base.historical_relationships import (
    delete_historical_relationships,
    ensure_historical_relationships,
)
from django.core.exceptions import ImproperlyConfigured
from django.db import migrations, models
from django.db.migrations.state import ProjectState
from rebac import ObjectRef, RelationshipTuple, SubjectRef

_FIELDS = {
    "context_content_type",
    "context_object_id",
    "reference_currency",
    "source_priority",
    "is_archived",
}
_NATIVE_FIELDS = {
    "id",
    "created_at",
    "updated_at",
    "currency",
    "date",
    "rate",
    "context_content_type",
    "context_object_id",
    "reference_currency",
    "source_priority",
    "is_archived",
}


def _constraint_fields(constraint: Any) -> tuple[str, ...]:
    return tuple(str(value) for value in getattr(constraint, "fields", ()))


def _constraint_named(constraints: tuple[Any, ...], name: str) -> Any | None:
    return next((value for value in constraints if value.name == name), None)


def applies(project_state: ProjectState) -> bool:
    """Apply only to the exact legacy global-rate schema."""

    rate = project_state.models.get(("money", "currencyrate"))
    if rate is None:
        return False
    present = _FIELDS & set(rate.fields)
    field = rate.fields.get("rate")
    constraints = tuple(rate.options.get("constraints", ()))
    old_unique = any(
        isinstance(value, models.UniqueConstraint)
        and _constraint_fields(value) == ("currency", "date")
        and value.name == "money_rate_currency_date"
        for value in constraints
    )
    complete = _constraint_named(constraints, "money_rate_context_complete")
    positive = _constraint_named(constraints, "money_rate_positive")
    priority = _constraint_named(constraints, "money_rate_source_priority")
    global_unique = _constraint_named(constraints, "money_rate_currency_date")
    contextual_unique = _constraint_named(
        constraints, "money_rate_context_currency_date"
    )
    if not present:
        if (
            isinstance(field, models.DecimalField)
            and field.max_digits == 20
            and field.decimal_places == 10
            and old_unique
        ):
            return True
        raise ImproperlyConfigured(
            "angee.money:contextual_currency_rates found an incompatible legacy rate schema."
        )
    if present != _FIELDS:
        raise ImproperlyConfigured(
            "angee.money:contextual_currency_rates found partial contextual fields."
        )
    context_type = rate.fields["context_content_type"]
    context_id = rate.fields["context_object_id"]
    reference = rate.fields["reference_currency"]
    source_priority = rate.fields["source_priority"]
    archived = rate.fields["is_archived"]
    if not (
        isinstance(field, models.DecimalField)
        and field.max_digits == 38
        and field.decimal_places == 20
        and isinstance(context_type, models.ForeignKey)
        and context_type.remote_field.model == "contenttypes.contenttype"
        and context_type.null
        and context_type.remote_field.on_delete is django.db.models.deletion.PROTECT
        and isinstance(context_id, models.CharField)
        and context_id.max_length == 255
        and context_id.blank
        and isinstance(reference, models.ForeignKey)
        and reference.remote_field.model == "money.currency"
        and reference.null
        and reference.remote_field.on_delete is django.db.models.deletion.PROTECT
        and isinstance(source_priority, models.PositiveSmallIntegerField)
        and source_priority.default == 0
        and isinstance(archived, models.BooleanField)
        and archived.default is False
        and isinstance(complete, models.CheckConstraint)
        and isinstance(positive, models.CheckConstraint)
        and isinstance(priority, models.CheckConstraint)
        and isinstance(global_unique, models.UniqueConstraint)
        and _constraint_fields(global_unique) == ("currency", "date")
        and isinstance(contextual_unique, models.UniqueConstraint)
        and _constraint_fields(contextual_unique)
        == ("currency", "date", "context_content_type", "context_object_id")
    ):
        raise ImproperlyConfigured(
            "angee.money:contextual_currency_rates found incompatible contextual fields."
        )
    return False


def _shared_rate_relationships(apps: Any, alias: str) -> tuple[RelationshipTuple, ...]:
    Rate = apps.get_model("money", "CurrencyRate")
    if {field.name for field in Rate._meta.fields} != _NATIVE_FIELDS:
        # A composed donor may own narrower visibility from persisted source
        # facts. Its migration must classify those rows atomically; the generic
        # schema transition stays default-deny rather than opening them first.
        return ()
    codec = SqidField(prefix="crt_")
    everyone = SubjectRef.of("auth/user", "*")
    return tuple(
        RelationshipTuple(
            resource=ObjectRef("money/rate", codec.public_id_from_value(pk)),
            relation="shared",
            subject=everyone,
        )
        for pk in Rate._base_manager.using(alias).filter(
            context_content_type__isnull=True,
            context_object_id="",
            reference_currency__isnull=True,
        ).values_list("pk", flat=True)
    )


def add_shared_rate_readers(apps: Any, schema_editor: Any) -> None:
    alias = schema_editor.connection.alias
    ensure_historical_relationships(
        apps,
        using=alias,
        relationships=_shared_rate_relationships(apps, alias),
    )


def remove_shared_rate_readers(apps: Any, schema_editor: Any) -> None:
    alias = schema_editor.connection.alias
    delete_historical_relationships(
        apps,
        using=alias,
        relationships=_shared_rate_relationships(apps, alias),
    )


class Migration(migrations.Migration):
    dependencies: ClassVar[list[tuple[str, str]]] = [
        ("contenttypes", "__latest__"),
        ("rebac", "__latest__"),
    ]
    operations: ClassVar[list[migrations.operations.base.Operation]] = [
        migrations.AlterField(
            model_name="currencyrate",
            name="rate",
            field=models.DecimalField(decimal_places=20, max_digits=38),
        ),
        migrations.AddField(
            model_name="currencyrate",
            name="context_content_type",
            field=models.ForeignKey(
                blank=True,
                editable=False,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to="contenttypes.contenttype",
            ),
        ),
        migrations.AddField(
            model_name="currencyrate",
            name="context_object_id",
            field=models.CharField(blank=True, editable=False, max_length=255),
        ),
        migrations.AddField(
            model_name="currencyrate",
            name="reference_currency",
            field=models.ForeignKey(
                blank=True,
                editable=False,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="contextual_rates",
                to="money.currency",
            ),
        ),
        migrations.AddField(
            model_name="currencyrate",
            name="source_priority",
            field=models.PositiveSmallIntegerField(default=0, editable=False),
        ),
        migrations.AddField(
            model_name="currencyrate",
            name="is_archived",
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.RemoveConstraint(
            model_name="currencyrate",
            name="money_rate_currency_date",
        ),
        migrations.AddConstraint(
            model_name="currencyrate",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        context_content_type__isnull=True,
                        context_object_id="",
                        reference_currency__isnull=True,
                    )
                    | (
                        models.Q(
                            context_content_type__isnull=False,
                            reference_currency__isnull=False,
                        )
                        & ~models.Q(context_object_id="")
                    )
                ),
                name="money_rate_context_complete",
            ),
        ),
        migrations.AddConstraint(
            model_name="currencyrate",
            constraint=models.CheckConstraint(
                condition=models.Q(rate__gt=0),
                name="money_rate_positive",
            ),
        ),
        migrations.AddConstraint(
            model_name="currencyrate",
            constraint=models.CheckConstraint(
                condition=models.Q(source_priority__gte=0),
                name="money_rate_source_priority",
            ),
        ),
        migrations.AddConstraint(
            model_name="currencyrate",
            constraint=models.UniqueConstraint(
                condition=models.Q(context_content_type__isnull=True),
                fields=("currency", "date"),
                name="money_rate_currency_date",
            ),
        ),
        migrations.AddConstraint(
            model_name="currencyrate",
            constraint=models.UniqueConstraint(
                condition=models.Q(context_content_type__isnull=False),
                fields=(
                    "currency",
                    "date",
                    "context_content_type",
                    "context_object_id",
                ),
                name="money_rate_context_currency_date",
            ),
        ),
        migrations.RunPython(add_shared_rate_readers, remove_shared_rate_readers),
    ]
