"""Tests for Angee model field types."""

from __future__ import annotations

import math
from importlib import import_module
from pathlib import Path
from typing import Any

import pytest
from django.apps import apps
from django.conf import settings
from django.core.exceptions import FieldError, ImproperlyConfigured, ValidationError
from django.db import connection, connections, models, router
from django.db.models import F, Value
from django.db.models.functions import Concat
from django.test.utils import isolate_apps, override_settings

from angee.base.fields.encrypted import EncryptedField, _derive_fernet
from angee.base.fields.identity import SqidField
from angee.base.fields.ordering import FractionalRankExhausted, FractionalRankField
from angee.base.fields.state import StateField
from angee.base.fields.text import DiagnosticTextField, ModelLabelField
from angee.base.models import SqidMixin
from tests.tables import model_tables


def test_diagnostic_text_requires_a_bound() -> None:
    with pytest.raises(ValueError, match="positive max_length"):
        DiagnosticTextField(max_length=None)


@pytest.mark.django_db(transaction=True)
def test_diagnostic_and_model_label_fields_normalize_instance_and_queryset_writes() -> None:
    """The fields own storage normalization, including QuerySet.update and filters."""

    class FieldOwnedText(models.Model):
        diagnostic = DiagnosticTextField(max_length=5)
        model_label = ModelLabelField(max_length=200)

        class Meta:
            app_label = "auth"

    with model_tables((FieldOwnedText,)):
        row = FieldOwnedText.objects.create(diagnostic="ab\x00cdef", model_label="auth.user")
        assert row.diagnostic == "abcde"
        assert row.model_label == "auth.User"
        FieldOwnedText.objects.filter(pk=row.pk).update(diagnostic="x\x00yz123", model_label="auth.user")
        assert FieldOwnedText.objects.filter(model_label="auth.user").get(pk=row.pk).diagnostic == "xyz12"
        assert FieldOwnedText.objects.filter(model_label__icontains="user").count() == 1
        assert not FieldOwnedText.objects.filter(model_label="auth.Missing").exists()
        with pytest.raises(ValidationError, match="Unknown model label"):
            FieldOwnedText.objects.filter(pk=row.pk).update(model_label="auth.Missing")


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("secret_key", ["field-encryption-key", b"field-encryption-key"])
@isolate_apps()
def test_encrypted_field_round_trips_plaintext(secret_key: str | bytes) -> None:
    """Plaintext assigned in Python decrypts when the row is reloaded."""

    class FieldRoundTrip(models.Model):
        """Concrete model used for encrypted field round-trip tests."""

        secret = EncryptedField()

        class Meta:
            """Django model options for the test model."""

            app_label = "auth"

    with override_settings(SECRET_KEY=secret_key), model_tables((FieldRoundTrip,)):
        instance = FieldRoundTrip.objects.create(secret="open sesame")

        reloaded = FieldRoundTrip.objects.get(pk=instance.pk)

        assert reloaded.secret == "open sesame"


@pytest.mark.django_db(transaction=True)
def test_encrypted_field_stores_ciphertext_at_rest() -> None:
    """The raw database value is encrypted and decryptable for its column."""

    class FieldCiphertext(models.Model):
        """Concrete model used for encrypted field storage tests."""

        secret = EncryptedField()

        class Meta:
            """Django model options for the test model."""

            app_label = "auth"

    with model_tables((FieldCiphertext,)):
        instance = FieldCiphertext.objects.create(secret="stored secret")
        table = connection.ops.quote_name(FieldCiphertext._meta.db_table)
        column = connection.ops.quote_name("secret")

        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT {column} FROM {table} WHERE id = %s",
                [instance.pk],
            )
            stored = cursor.fetchone()[0]

        label = f"{FieldCiphertext._meta.label_lower}.secret"
        assert stored != "stored secret"
        assert _derive_fernet(label).decrypt(stored.encode()).decode() == "stored secret"


@pytest.mark.django_db(transaction=True)
def test_encrypted_field_preserves_none() -> None:
    """Null values pass through save and load unchanged."""

    class FieldOptional(models.Model):
        """Concrete model used for encrypted field null tests."""

        secret = EncryptedField(null=True)

        class Meta:
            """Django model options for the test model."""

            app_label = "auth"

    with model_tables((FieldOptional,)):
        instance = FieldOptional.objects.create(secret=None)

        reloaded = FieldOptional.objects.get(pk=instance.pk)

        assert reloaded.secret is None


def test_encrypted_field_deconstruct_is_stable_and_value_free() -> None:
    """Django's field deconstruction does not include encryption material."""

    class FieldDeconstruct(models.Model):
        """Concrete model used for encrypted field deconstruction tests."""

        secret = EncryptedField()

        class Meta:
            """Django model options for the test model."""

            app_label = "auth"

    field = FieldDeconstruct._meta.get_field("secret")

    first = field.deconstruct()
    second = field.deconstruct()

    assert first == second
    assert settings.SECRET_KEY not in repr(first)


def test_encrypted_field_caches_bound_fernet(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bound encrypted field derives its Fernet once per model column."""

    class FieldCachedFernet(models.Model):
        """Concrete model used for encrypted field cache tests."""

        secret = EncryptedField()

        class Meta:
            """Django model options for the test model."""

            app_label = "auth"

    field = FieldCachedFernet._meta.get_field("secret")
    calls: list[str] = []

    def derive(label: str) -> Any:
        calls.append(label)
        return _derive_fernet(label)

    monkeypatch.setattr("angee.base.fields.encrypted._derive_fernet", derive)

    first = field._fernet()
    second = field._fernet()

    assert first is second
    assert calls == [f"{FieldCachedFernet._meta.label_lower}.secret"]


def test_encrypted_field_rejects_unique_and_primary_key() -> None:
    """Unique encrypted columns cannot enforce plaintext uniqueness."""

    with pytest.raises(ImproperlyConfigured, match="cannot be unique or a primary key"):
        EncryptedField(unique=True)
    with pytest.raises(ImproperlyConfigured, match="cannot be unique or a primary key"):
        EncryptedField(primary_key=True)


@pytest.mark.django_db(transaction=True)
def test_encrypted_field_rejects_expression_writes() -> None:
    """SQL expressions cannot be encrypted in flight."""

    class FieldExpression(models.Model):
        """Concrete model used for encrypted field expression-write tests."""

        secret = EncryptedField()
        other_text_field = models.TextField()

        class Meta:
            """Django model options for the test model."""

            app_label = "auth"

    with model_tables((FieldExpression,)):
        instance = FieldExpression.objects.create(
            secret="initial",
            other_text_field="other",
        )

        with pytest.raises(FieldError, match="expression writes"):
            FieldExpression.objects.filter(pk=instance.pk).update(
                secret=F("other_text_field"),
            )
        with pytest.raises(FieldError, match="expression writes"):
            FieldExpression.objects.filter(pk=instance.pk).update(
                secret=Concat("other_text_field", Value("-suffix")),
            )


@pytest.mark.django_db(transaction=True)
def test_encrypted_field_rejects_bulk_update() -> None:
    """bulk_update wraps literal values in SQL expressions."""

    class FieldBulkUpdate(models.Model):
        """Concrete model used for encrypted field bulk-update tests."""

        secret = EncryptedField()

        class Meta:
            """Django model options for the test model."""

            app_label = "auth"

    with model_tables((FieldBulkUpdate,)):
        instance = FieldBulkUpdate.objects.create(secret="initial")
        instance.secret = "updated"

        with pytest.raises(FieldError, match="bulk_update"):
            FieldBulkUpdate.objects.bulk_update([instance], ["secret"])


@pytest.mark.django_db(transaction=True)
def test_encrypted_field_refresh_from_db_reads_literal_update() -> None:
    """A literal queryset update stores encrypted text that refreshes cleanly."""

    class FieldRefresh(models.Model):
        """Concrete model used for encrypted field refresh tests."""

        secret = EncryptedField()

        class Meta:
            """Django model options for the test model."""

            app_label = "auth"

    with model_tables((FieldRefresh,)):
        instance = FieldRefresh.objects.create(secret="old")

        FieldRefresh.objects.filter(pk=instance.pk).update(secret="new")
        instance.refresh_from_db()

        assert instance.secret == "new"


@pytest.mark.django_db(transaction=True)
def test_encrypted_field_double_save_round_trips_plaintext() -> None:
    """Reloaded plaintext saves as plaintext again, not as nested ciphertext."""

    class FieldDoubleSave(models.Model):
        """Concrete model used for encrypted field double-save tests."""

        secret = EncryptedField()

        class Meta:
            """Django model options for the test model."""

            app_label = "auth"

    with model_tables((FieldDoubleSave,)):
        instance = FieldDoubleSave.objects.create(secret="stable")
        reloaded = FieldDoubleSave.objects.get(pk=instance.pk)

        reloaded.save()
        saved_again = FieldDoubleSave.objects.get(pk=instance.pk)

        assert saved_again.secret == "stable"


@pytest.mark.django_db(transaction=True)
def test_encrypted_field_literal_update_stores_ciphertext() -> None:
    """A string literal queryset update encrypts the stored column value."""

    class FieldLiteralUpdate(models.Model):
        """Concrete model used for encrypted field literal-update tests."""

        secret = EncryptedField()

        class Meta:
            """Django model options for the test model."""

            app_label = "auth"

    with model_tables((FieldLiteralUpdate,)):
        instance = FieldLiteralUpdate.objects.create(secret="initial")
        FieldLiteralUpdate.objects.filter(pk=instance.pk).update(secret="updated")
        table = connection.ops.quote_name(FieldLiteralUpdate._meta.db_table)
        column = connection.ops.quote_name("secret")

        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT {column} FROM {table} WHERE id = %s",
                [instance.pk],
            )
            stored = cursor.fetchone()[0]

        label = f"{FieldLiteralUpdate._meta.label_lower}.secret"
        assert stored != "updated"
        assert _derive_fernet(label).decrypt(stored.encode()).decode() == "updated"


@pytest.mark.django_db(transaction=True)
def test_encrypted_field_filter_by_value_is_loud_but_isnull_works() -> None:
    """Encrypted values cannot be filtered by plaintext value."""

    class FieldFilter(models.Model):
        """Concrete model used for encrypted field lookup tests."""

        secret = EncryptedField()

        class Meta:
            """Django model options for the test model."""

            app_label = "auth"

    with model_tables((FieldFilter,)):
        instance = FieldFilter.objects.create(secret="x")

        with pytest.raises(FieldError, match="not queryable by value"):
            FieldFilter.objects.filter(secret="x").exists()

        assert FieldFilter.objects.filter(secret__isnull=False).get() == instance


@pytest.mark.django_db(transaction=True)
def test_encrypted_field_wraps_invalid_ciphertext_errors() -> None:
    """ORM reads report invalid ciphertext as an actionable field error."""

    class FieldInvalidCiphertext(models.Model):
        """Concrete model used for encrypted field invalid-ciphertext tests."""

        secret = EncryptedField()

        class Meta:
            """Django model options for the test model."""

            app_label = "auth"

    with model_tables((FieldInvalidCiphertext,)):
        instance = FieldInvalidCiphertext.objects.create(secret="valid")
        table = connection.ops.quote_name(FieldInvalidCiphertext._meta.db_table)
        column = connection.ops.quote_name("secret")

        with connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE {table} SET {column} = %s WHERE id = %s",
                ["not encrypted", instance.pk],
            )

        reloaded = FieldInvalidCiphertext.objects.get(pk=instance.pk)

        with pytest.raises(
            ImproperlyConfigured,
            match=f"Cannot decrypt {FieldInvalidCiphertext._meta.label_lower}",
        ):
            reloaded.secret


@pytest.mark.django_db(transaction=True)
def test_encrypted_field_corrupt_row_does_not_break_queryset() -> None:
    """One bad encrypted value is isolated to the row field access."""

    class FieldCorruptRow(models.Model):
        """Concrete model used for encrypted field corrupt-row tests."""

        secret = EncryptedField()

        class Meta:
            """Django model options for the test model."""

            app_label = "auth"

    with model_tables((FieldCorruptRow,)):
        valid = FieldCorruptRow.objects.create(secret="valid")
        corrupt = FieldCorruptRow.objects.create(secret="corrupt")
        table = connection.ops.quote_name(FieldCorruptRow._meta.db_table)
        column = connection.ops.quote_name("secret")

        with connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE {table} SET {column} = %s WHERE id = %s",
                ["not encrypted", corrupt.pk],
            )

        rows = list(FieldCorruptRow.objects.order_by("pk"))

        assert rows[0].pk == valid.pk
        assert rows[0].secret == "valid"
        assert rows[1].pk == corrupt.pk
        with pytest.raises(ImproperlyConfigured, match=f"Cannot decrypt {FieldCorruptRow._meta.label_lower}"):
            rows[1].secret


@pytest.mark.parametrize("db_index", [True, False])
def test_state_field_deconstruct_and_clone_preserve_index_choice(db_index: bool) -> None:
    """Migration state cloning must not introduce an index on an opted-out column."""

    class Status(models.TextChoices):
        ENABLED = "enabled", "Enabled"

    field = StateField(choices_enum=Status, db_index=db_index)
    declaration = field.deconstruct()

    assert declaration[3]["db_index"] is db_index
    cloned = field.clone()
    assert cloned.db_index is db_index
    assert cloned.deconstruct() == declaration


def test_state_field_status_display_is_presentation_only() -> None:
    """The status shape reaches metadata without entering migration state."""

    class Status(models.TextChoices):
        ENABLED = "enabled", "Enabled"

    field = StateField(choices_enum=Status, status_display="dot")

    assert field.angee_status_display == "dot"
    assert "status_display" not in field.deconstruct()[3]


def test_active_model_state_fields_pass_checks() -> None:
    """Concrete composed fields satisfy the optional-state declaration rule."""

    errors = [
        error
        for model in apps.get_models()
        for field in model._meta.get_fields()
        if isinstance(field, StateField)
        for error in field.check()
    ]
    assert not errors


@pytest.mark.parametrize(
    ("blank", "nullable", "is_abstract", "default", "expected_errors"),
    [
        (True, False, False, "", ["angee.E019"]),
        (True, False, False, "enabled", ["angee.E019"]),
        (True, True, False, None, []),
        (False, False, False, "enabled", []),
        (True, False, True, "", []),
    ],
)
@isolate_apps()
def test_state_field_check_requires_null_for_concrete_optional_states(
    blank: bool,
    nullable: bool,
    is_abstract: bool,
    default: str | None,
    expected_errors: list[str],
) -> None:
    """Model checks reject optional-state sentinels, including real defaults."""

    class OptionalKind(models.TextChoices):
        ENABLED = "enabled", "Enabled"

    class CheckedState(models.Model):
        state = StateField(choices_enum=OptionalKind, blank=blank, null=nullable, default=default)

        class Meta:
            app_label = "tests"
            abstract = is_abstract

    field = CheckedState._meta.get_field("state")
    errors = field.check()
    assert [error.id for error in errors] == expected_errors
    if errors:
        assert errors[0].obj is field
        assert "migrate stored empty strings to NULL" in errors[0].hint
        assert any(error.id == "angee.E019" for error in CheckedState.check())


@pytest.mark.django_db(transaction=True)
def test_sqid_field_passes_null_joins_through() -> None:
    """A nullable-FK join selecting ``__sqid`` yields None instead of raising.

    This is the exact query REBAC field-backed arrows run over optional
    parents (e.g. ``// rebac:field=parent``), which the raw django-sqids
    field crashes on.
    """

    class SqidNode(models.Model):
        """Concrete self-referencing model used for sqid join tests."""

        sqid = SqidField(real_field_name="id", prefix="tst_", min_length=8)
        parent = models.ForeignKey("self", on_delete=models.CASCADE, null=True)

        class Meta:
            """Django model options for the test model."""

            app_label = "auth"

    with model_tables((SqidNode,)):
        root = SqidNode.objects.create()
        child = SqidNode.objects.create(parent=root)

        values = dict(SqidNode.objects.values_list("pk", "parent__sqid"))

        assert values[root.pk] is None
        assert values[child.pk] == root.sqid
        assert str(root.sqid).startswith("tst_")


@pytest.mark.django_db(transaction=True)
def test_sqid_field_canonical_prefix_uses_separator() -> None:
    """Bare declarations still expose public ids as ``prefix_value``."""

    class BarePrefixNode(models.Model):
        """Concrete model used for prefix normalization tests."""

        sqid = SqidField(real_field_name="id", prefix="bare", min_length=8)

        class Meta:
            """Django model options for the test model."""

            app_label = "auth"

    with model_tables((BarePrefixNode,)):
        node = BarePrefixNode.objects.create()

        assert BarePrefixNode._meta.get_field("sqid").prefix == "bare_"
        assert str(node.sqid).startswith("bare_")
        assert BarePrefixNode.objects.get(sqid=node.sqid) == node
        assert BarePrefixNode.objects.filter(sqid=str(node.sqid).replace("bare_", "bare", 1)).first() is None


def test_sqid_field_deconstruct_preserves_public_id_contract() -> None:
    """Generated/runtime model state carries sqid prefix and encoder settings."""

    field = SqidField(real_field_name="id", prefix="abc_", min_length=8)
    _, _, _, kwargs = field.deconstruct()

    assert field.prefix == "abc_"
    assert kwargs["prefix"] == "abc_"
    assert kwargs["real_field_name"] == "id"
    assert kwargs["min_length"] == 8


@pytest.mark.parametrize(
    ("module", "model_name", "prefix"),
    [
        *(
            ("angee.parties.models", name, prefix)
            for name, prefix in (
                ("Party", "pty_"),
                ("Handle", "hdl_"),
                ("Address", "adr_"),
                ("Folder", "fol_"),
            )
        ),
        *(
            ("angee.messaging.models", name, prefix)
            for name, prefix in (
                ("Thread", "thr_"),
                ("Message", "msg_"),
                ("Fragment", "frg_"),
                ("Part", "prt_"),
                ("MessageEdge", "mge_"),
                ("Participant", "ptp_"),
                ("Reaction", "rxn_"),
                ("MessageStar", "msr_"),
            )
        ),
    ],
)
def test_declared_sqid_prefix_keeps_explicit_field_deconstruction(
    module: str,
    model_name: str,
    prefix: str,
) -> None:
    """The removed redeclarations leave Django's field state unchanged."""

    model = getattr(import_module(module), model_name)
    explicit = SqidField(real_field_name="id", prefix=prefix, min_length=8)
    explicit.set_attributes_from_name("sqid")

    assert model._meta.get_field("sqid").deconstruct() == explicit.deconstruct()


@pytest.mark.django_db(transaction=True)
def test_sqid_mixin_resolves_prefix_from_sqid_prefix_attr() -> None:
    """A model states only ``sqid_prefix``; the shared field reads it."""

    class PrefixedThing(SqidMixin):
        """Concrete model declaring only its public-id prefix."""

        sqid_prefix = "abc_"

        class Meta:
            """Django model options for the test model."""

            app_label = "auth"

    with model_tables((PrefixedThing,)):
        thing = PrefixedThing.objects.create()

        assert PrefixedThing._meta.get_field("sqid").prefix == "abc_"
        assert str(thing.sqid).startswith("abc_")


def test_sqid_field_rejects_non_string_prefix() -> None:
    """A non-string ``sqid_prefix`` fails loudly at class definition."""

    with pytest.raises(ImproperlyConfigured, match="sqid_prefix must be a str"):

        class BadPrefixThing(SqidMixin):
            """Model misconfiguring its public-id prefix."""

            sqid_prefix = 5  # type: ignore[assignment]

            class Meta:
                """Django model options for the test model."""

                app_label = "auth"


@pytest.mark.parametrize(
    "options",
    [
        {},
        {"db_index": False},
        {"db_default": models.NOT_PROVIDED},
        {"db_index": False, "db_default": models.NOT_PROVIDED},
    ],
)
def test_fractional_rank_deconstruct_round_trips_constructor_defaults_and_opt_outs(options: dict[str, Any]) -> None:
    """Migration serialization preserves defaults as well as explicit opt-outs."""

    field = FractionalRankField(**options)
    _, path, args, kwargs = field.deconstruct()
    assert kwargs == options
    rebuilt = import_module(path.rsplit(".", 1)[0]).FractionalRankField(*args, **kwargs)
    assert rebuilt.db_index == field.db_index
    assert rebuilt.db_default == field.db_default
    assert rebuilt.deconstruct() == field.deconstruct()


@pytest.mark.django_db(transaction=True)
def test_fractional_rank_appends_within_its_unique_context(monkeypatch: pytest.MonkeyPatch) -> None:
    """An omitted rank appends per context; an explicit rank is honored."""

    class RankedItem(models.Model):
        """Concrete model used for fractional-rank allocation tests."""

        lane = models.CharField(max_length=8)
        rank = FractionalRankField()

        class Meta:
            """Django model options for the test model."""

            app_label = "auth"
            constraints = (models.UniqueConstraint(fields=("lane", "rank"), name="ranked_item_lane_rank"),)

    with model_tables((RankedItem,)):
        # Exercise Django's PostgreSQL transaction guard on the SQLite floor too.
        assert connection.get_autocommit()
        with monkeypatch.context() as compiler:
            compiler.setattr(connection.features, "has_select_for_update", True)
            first = RankedItem.objects.create(lane="a")
            second = RankedItem.objects.create(lane="a")
            other_lane = RankedItem.objects.create(lane="b")
            explicit = RankedItem.objects.create(lane="a", rank=512.0)

        assert first.rank == FractionalRankField.STEP
        assert second.rank == FractionalRankField.STEP * 2
        assert other_lane.rank == FractionalRankField.STEP
        assert explicit.rank == 512.0
        after_explicit = RankedItem.objects.create(lane="a")
        assert after_explicit.rank == second.rank + FractionalRankField.STEP


@pytest.mark.django_db(transaction=True)
def test_fractional_reordering_reserves_the_complete_context_and_destinations() -> None:
    """A partial reorder avoids unseen rows and requested ranks in its context."""

    class ReorderedItem(models.Model):
        lane = models.CharField(max_length=8)
        rank = FractionalRankField()

        class Meta:
            app_label = "auth"
            constraints = (models.UniqueConstraint(fields=("lane", "rank"), name="reordered_item_lane_rank"),)

    with model_tables((ReorderedItem,)):
        first = ReorderedItem.objects.create(lane="a", rank=0)
        second = ReorderedItem.objects.create(lane="a", rank=1)
        ReorderedItem.objects.create(lane="a", rank=9000)
        ReorderedItem.objects.create(lane="other", rank=math.nextafter(math.inf, 0.0))
        field = ReorderedItem._meta.get_field("rank")
        before = list(ReorderedItem.objects.order_by("pk").values_list("pk", "rank"))
        allocated = field.get_temporary_ranks([first, second], [20000, None], using=connection.alias)
        assert len(set(allocated)) == 2
        assert all(math.isfinite(rank) and rank > 20000 for rank in allocated)
        with pytest.raises(FractionalRankExhausted):
            field.get_temporary_ranks([first, second], [math.nextafter(math.inf, 0.0)], using=connection.alias)
        assert list(ReorderedItem.objects.order_by("pk").values_list("pk", "rank")) == before


@pytest.mark.django_db(transaction=True)
def test_fractional_rank_column_backfills_existing_rows_without_changing_allocation() -> None:
    """Schema backfill uses STEP; later ORM inserts still append in context."""

    class BeforeRank(models.Model):
        """Existing ungrouped rows before the rank column is introduced."""

        lane = models.CharField(max_length=8, null=True)

        class Meta:
            app_label = "auth"
            db_table = "auth_rank_backfill"

    class AfterRank(models.Model):
        """The migrated model state for the same table."""

        lane = models.CharField(max_length=8, null=True)
        rank = FractionalRankField()

        class Meta:
            app_label = "auth"
            db_table = "auth_rank_backfill"
            constraints = (models.UniqueConstraint(fields=("lane", "rank"), name="rank_backfill_lane_rank"),)

    with model_tables((BeforeRank,)):
        BeforeRank.objects.bulk_create([BeforeRank(), BeforeRank()])
        with connection.schema_editor() as editor:
            editor.add_field(BeforeRank, AfterRank._meta.get_field("rank"))
        with connection.schema_editor() as editor:
            editor.add_constraint(AfterRank, AfterRank._meta.constraints[0])

        assert list(AfterRank.objects.values_list("rank", flat=True)) == [FractionalRankField.STEP] * 2
        appended = AfterRank.objects.create()
        assert appended.rank == FractionalRankField.STEP * 2


@pytest.mark.django_db(transaction=True)
def test_fractional_rank_full_clean_allows_the_pending_none() -> None:
    """``full_clean`` before save passes with the rank still unallocated."""

    class RankedPending(models.Model):
        """Concrete model used for pending-rank validation tests."""

        lane = models.CharField(max_length=8)
        rank = FractionalRankField()

        class Meta:
            """Django model options for the test model."""

            app_label = "auth"
            constraints = (models.UniqueConstraint(fields=("lane", "rank"), name="ranked_pending_lane_rank"),)

    with model_tables((RankedPending,)):
        instance = RankedPending(lane="a")

        instance.full_clean()
        instance.save()

        assert instance.rank == FractionalRankField.STEP


@pytest.mark.django_db(transaction=True)
def test_fractional_rank_requires_exactly_one_unique_context() -> None:
    """Allocation fails loudly without exactly one field-based unique context."""

    class RankedUnconstrained(models.Model):
        """Concrete model whose rank declares no unique context."""

        rank = FractionalRankField()

        class Meta:
            """Django model options for the test model."""

            app_label = "auth"

    class RankedOverconstrained(models.Model):
        """Concrete model whose rank belongs to two unique contexts."""

        lane = models.CharField(max_length=8)
        tier = models.CharField(max_length=8)
        rank = FractionalRankField()

        class Meta:
            """Django model options for the test model."""

            app_label = "auth"
            constraints = (
                models.UniqueConstraint(fields=("lane", "rank"), name="ranked_over_lane_rank"),
                models.UniqueConstraint(fields=("tier", "rank"), name="ranked_over_tier_rank"),
            )

    with model_tables((RankedUnconstrained, RankedOverconstrained)):
        with pytest.raises(ImproperlyConfigured, match="exactly one"):
            RankedUnconstrained.objects.create()
        with pytest.raises(ImproperlyConfigured, match="exactly one"):
            RankedOverconstrained.objects.create(lane="a", tier="x")

        explicit = RankedUnconstrained.objects.create(rank=64.0)
        assert explicit.rank == 64.0


def test_fractional_rank_has_default_exposes_the_server_allocator() -> None:
    """The Hasura-facing default facts advertise omittable inserts."""

    field = FractionalRankField()

    assert field.has_default() is True
    assert field.get_default() is None


def test_get_append_rank_steps_and_exhausts() -> None:
    """Append ranks start at STEP, step cleanly, and exhaust loudly."""

    step = FractionalRankField.STEP

    assert FractionalRankField.get_append_rank(None) == step
    assert FractionalRankField.get_append_rank(step) == step * 2
    with pytest.raises(FractionalRankExhausted):
        FractionalRankField.get_append_rank(float("1e308") * 1.7976)


def test_fractional_rank_rejects_non_finite_values() -> None:
    """NaN and infinity are refused on both coercion boundaries."""

    field = FractionalRankField()

    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValidationError):
            field.to_python(bad)
        with pytest.raises(ValidationError):
            field.get_prep_value(bad)


@pytest.mark.django_db(transaction=True)
def test_fractional_rank_allocation_uses_explicit_write_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, django_db_blocker: Any,
) -> None:
    """Explicit save/create/bulk aliases own allocation despite a different router."""

    class AliasRank(models.Model):
        lane = models.CharField(max_length=20)
        rank = FractionalRankField()

        class Meta:
            app_label = "auth"
            constraints = (models.UniqueConstraint(fields=("lane", "rank"), name="alias_rank_lane_rank"),)

    alias = "rank_explicit_probe"

    class DefaultRouter:
        def db_for_read(self, model: type[models.Model], **hints: Any) -> str:
            return "default"

        db_for_write = db_for_read

    monkeypatch.setattr(router, "routers", [DefaultRouter()])
    connections.databases[alias] = {
        **connections.databases["default"],
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": str(tmp_path / "explicit-rank.sqlite3"),
        "OPTIONS": {},
        "CONN_MAX_AGE": 0,
    }
    database = connections[alias]
    events: list[tuple[str, float | None, str]] = []

    def before_save(sender: Any, instance: Any, using: str, **kwargs: Any) -> None:
        events.append(("before", instance.rank, using))

    def after_save(sender: Any, instance: Any, using: str, **kwargs: Any) -> None:
        events.append(("after", instance.rank, using))

    try:
        with django_db_blocker.unblock(), model_tables((AliasRank,)):
            with database.schema_editor() as editor:
                editor.create_model(AliasRank)
            for lane, tail in (("create", 100.0), ("save", 100.0), ("bulk_a", 100.0), ("bulk_b", 200.0)):
                AliasRank.objects.create(lane=lane, rank=10.0)
                AliasRank.objects.using(alias).create(lane=lane, rank=tail)
            models.signals.pre_save.connect(before_save, sender=AliasRank, weak=False)
            models.signals.post_save.connect(after_save, sender=AliasRank, weak=False)
            created = AliasRank.objects.using(alias).create(lane="create")
            saved = AliasRank(lane="save")
            saved.save(using=alias)
            expected = FractionalRankField.STEP + 100.0
            assert created.rank == saved.rank == expected
            assert events == [
                ("before", None, alias), ("after", expected, alias),
                ("before", None, alias), ("after", expected, alias),
            ]
            rows = [AliasRank(lane="bulk_a"), AliasRank(lane="bulk_b"), AliasRank(lane="explicit", rank=7.0)]
            AliasRank.objects.using(alias).bulk_create(rows)
            assert [row.rank for row in rows] == [expected, FractionalRankField.STEP + 200.0, 7.0]
            stored = AliasRank.objects.using(alias).filter(pk__in=[row.pk for row in rows]).order_by("lane")
            assert list(stored.values_list("rank", flat=True)) == [expected, FractionalRankField.STEP + 200.0, 7.0]
            field = AliasRank._meta.get_field("rank")
            temporary = field.get_temporary_ranks([created, saved], [0, 1], using=alias)
            assert min(temporary) > expected
            AliasRank.objects.using(alias).create(lane="exhausted", rank=math.nextafter(math.inf, 0.0))
            exhausted = AliasRank(lane="exhausted")
            with pytest.raises(FractionalRankExhausted):
                exhausted.save(using=alias)
            assert exhausted.rank is None
            assert AliasRank.objects.using(alias).filter(lane="exhausted").count() == 1
    finally:
        models.signals.pre_save.disconnect(before_save, sender=AliasRank)
        models.signals.post_save.disconnect(after_save, sender=AliasRank)
        database.close()
        del connections[alias]
        del connections.databases[alias]




@isolate_apps("django.contrib.contenttypes")
def test_state_field_does_not_cache_unresolved_pending_foreign_key() -> None:
    """Forward guard discovery must not resolve unrelated reverse metadata early."""

    class Status(models.TextChoices):
        OPEN = "open", "Open"

    class StateParent(models.Model):
        state = StateField(choices_enum=Status, default=Status.OPEN)

        class Meta:
            app_label = "contenttypes"
            abstract = True

    class Waiting(models.Model):
        target = models.ForeignKey("contenttypes.StateTarget", on_delete=models.CASCADE)

        class Meta:
            app_label = "contenttypes"

    class StateTarget(StateParent):
        extra_state = StateField(choices_enum=Status, default=Status.OPEN)

        class Meta:
            app_label = "contenttypes"

    relation = Waiting._meta.get_field("target")
    assert relation.remote_field.model is StateTarget
    assert relation.related_model is StateTarget
