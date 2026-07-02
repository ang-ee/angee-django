"""Tests for Angee model field types."""

from __future__ import annotations

from typing import Any

import pytest
from django.conf import settings
from django.core.exceptions import FieldError, ImproperlyConfigured
from django.db import connection, models
from django.db.models import F, Value
from django.db.models.functions import Concat

from angee.base.fields import EncryptedField, SqidField, StateField, _derive_fernet
from angee.base.mixins import SqidMixin


@pytest.mark.django_db(transaction=True)
def test_encrypted_field_round_trips_plaintext() -> None:
    """Plaintext assigned in Python decrypts when the row is reloaded."""

    class FieldRoundTrip(models.Model):
        """Concrete model used for encrypted field round-trip tests."""

        secret = EncryptedField()

        class Meta:
            """Django model options for the test model."""

            app_label = "auth"

    with connection.schema_editor() as schema_editor:
        schema_editor.create_model(FieldRoundTrip)
    try:
        instance = FieldRoundTrip.objects.create(secret="open sesame")

        reloaded = FieldRoundTrip.objects.get(pk=instance.pk)

        assert reloaded.secret == "open sesame"
    finally:
        with connection.schema_editor() as schema_editor:
            schema_editor.delete_model(FieldRoundTrip)


@pytest.mark.django_db(transaction=True)
def test_encrypted_field_stores_ciphertext_at_rest() -> None:
    """The raw database value is encrypted and decryptable for its column."""

    class FieldCiphertext(models.Model):
        """Concrete model used for encrypted field storage tests."""

        secret = EncryptedField()

        class Meta:
            """Django model options for the test model."""

            app_label = "auth"

    with connection.schema_editor() as schema_editor:
        schema_editor.create_model(FieldCiphertext)
    try:
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
    finally:
        with connection.schema_editor() as schema_editor:
            schema_editor.delete_model(FieldCiphertext)


@pytest.mark.django_db(transaction=True)
def test_encrypted_field_preserves_none() -> None:
    """Null values pass through save and load unchanged."""

    class FieldOptional(models.Model):
        """Concrete model used for encrypted field null tests."""

        secret = EncryptedField(null=True)

        class Meta:
            """Django model options for the test model."""

            app_label = "auth"

    with connection.schema_editor() as schema_editor:
        schema_editor.create_model(FieldOptional)
    try:
        instance = FieldOptional.objects.create(secret=None)

        reloaded = FieldOptional.objects.get(pk=instance.pk)

        assert reloaded.secret is None
    finally:
        with connection.schema_editor() as schema_editor:
            schema_editor.delete_model(FieldOptional)


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

    monkeypatch.setattr("angee.base.fields._derive_fernet", derive)

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

    with connection.schema_editor() as schema_editor:
        schema_editor.create_model(FieldExpression)
    try:
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
    finally:
        with connection.schema_editor() as schema_editor:
            schema_editor.delete_model(FieldExpression)


@pytest.mark.django_db(transaction=True)
def test_encrypted_field_rejects_bulk_update() -> None:
    """bulk_update wraps literal values in SQL expressions."""

    class FieldBulkUpdate(models.Model):
        """Concrete model used for encrypted field bulk-update tests."""

        secret = EncryptedField()

        class Meta:
            """Django model options for the test model."""

            app_label = "auth"

    with connection.schema_editor() as schema_editor:
        schema_editor.create_model(FieldBulkUpdate)
    try:
        instance = FieldBulkUpdate.objects.create(secret="initial")
        instance.secret = "updated"

        with pytest.raises(FieldError, match="bulk_update"):
            FieldBulkUpdate.objects.bulk_update([instance], ["secret"])
    finally:
        with connection.schema_editor() as schema_editor:
            schema_editor.delete_model(FieldBulkUpdate)


@pytest.mark.django_db(transaction=True)
def test_encrypted_field_refresh_from_db_reads_literal_update() -> None:
    """A literal queryset update stores encrypted text that refreshes cleanly."""

    class FieldRefresh(models.Model):
        """Concrete model used for encrypted field refresh tests."""

        secret = EncryptedField()

        class Meta:
            """Django model options for the test model."""

            app_label = "auth"

    with connection.schema_editor() as schema_editor:
        schema_editor.create_model(FieldRefresh)
    try:
        instance = FieldRefresh.objects.create(secret="old")

        FieldRefresh.objects.filter(pk=instance.pk).update(secret="new")
        instance.refresh_from_db()

        assert instance.secret == "new"
    finally:
        with connection.schema_editor() as schema_editor:
            schema_editor.delete_model(FieldRefresh)


@pytest.mark.django_db(transaction=True)
def test_encrypted_field_double_save_round_trips_plaintext() -> None:
    """Reloaded plaintext saves as plaintext again, not as nested ciphertext."""

    class FieldDoubleSave(models.Model):
        """Concrete model used for encrypted field double-save tests."""

        secret = EncryptedField()

        class Meta:
            """Django model options for the test model."""

            app_label = "auth"

    with connection.schema_editor() as schema_editor:
        schema_editor.create_model(FieldDoubleSave)
    try:
        instance = FieldDoubleSave.objects.create(secret="stable")
        reloaded = FieldDoubleSave.objects.get(pk=instance.pk)

        reloaded.save()
        saved_again = FieldDoubleSave.objects.get(pk=instance.pk)

        assert saved_again.secret == "stable"
    finally:
        with connection.schema_editor() as schema_editor:
            schema_editor.delete_model(FieldDoubleSave)


@pytest.mark.django_db(transaction=True)
def test_encrypted_field_literal_update_stores_ciphertext() -> None:
    """A string literal queryset update encrypts the stored column value."""

    class FieldLiteralUpdate(models.Model):
        """Concrete model used for encrypted field literal-update tests."""

        secret = EncryptedField()

        class Meta:
            """Django model options for the test model."""

            app_label = "auth"

    with connection.schema_editor() as schema_editor:
        schema_editor.create_model(FieldLiteralUpdate)
    try:
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
    finally:
        with connection.schema_editor() as schema_editor:
            schema_editor.delete_model(FieldLiteralUpdate)


@pytest.mark.django_db(transaction=True)
def test_encrypted_field_filter_by_value_is_loud_but_isnull_works() -> None:
    """Encrypted values cannot be filtered by plaintext value."""

    class FieldFilter(models.Model):
        """Concrete model used for encrypted field lookup tests."""

        secret = EncryptedField()

        class Meta:
            """Django model options for the test model."""

            app_label = "auth"

    with connection.schema_editor() as schema_editor:
        schema_editor.create_model(FieldFilter)
    try:
        instance = FieldFilter.objects.create(secret="x")

        with pytest.raises(FieldError, match="not queryable by value"):
            FieldFilter.objects.filter(secret="x").exists()

        assert FieldFilter.objects.filter(secret__isnull=False).get() == instance
    finally:
        with connection.schema_editor() as schema_editor:
            schema_editor.delete_model(FieldFilter)


@pytest.mark.django_db(transaction=True)
def test_encrypted_field_wraps_invalid_ciphertext_errors() -> None:
    """ORM reads report invalid ciphertext as an actionable field error."""

    class FieldInvalidCiphertext(models.Model):
        """Concrete model used for encrypted field invalid-ciphertext tests."""

        secret = EncryptedField()

        class Meta:
            """Django model options for the test model."""

            app_label = "auth"

    with connection.schema_editor() as schema_editor:
        schema_editor.create_model(FieldInvalidCiphertext)
    try:
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
    finally:
        with connection.schema_editor() as schema_editor:
            schema_editor.delete_model(FieldInvalidCiphertext)


@pytest.mark.django_db(transaction=True)
def test_encrypted_field_corrupt_row_does_not_break_queryset() -> None:
    """One bad encrypted value is isolated to the row field access."""

    class FieldCorruptRow(models.Model):
        """Concrete model used for encrypted field corrupt-row tests."""

        secret = EncryptedField()

        class Meta:
            """Django model options for the test model."""

            app_label = "auth"

    with connection.schema_editor() as schema_editor:
        schema_editor.create_model(FieldCorruptRow)
    try:
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
    finally:
        with connection.schema_editor() as schema_editor:
            schema_editor.delete_model(FieldCorruptRow)


@pytest.mark.django_db(transaction=True)
def test_state_field_supports_blank_string_states() -> None:
    """StateField owns nullable-free blank-string state columns."""

    class OptionalKind(models.TextChoices):
        """Finite states for blank-compatible state-field tests."""

        ENABLED = "enabled", "Enabled"

    class FieldBlankState(models.Model):
        """Concrete model used for blank-compatible state tests."""

        state = StateField(choices_enum=OptionalKind, default="", blank=True)

        class Meta:
            """Django model options for the test model."""

            app_label = "auth"

    with connection.schema_editor() as schema_editor:
        schema_editor.create_model(FieldBlankState)
    try:
        blank = FieldBlankState.objects.create()
        enabled = FieldBlankState.objects.create(state="ENABLED")

        assert FieldBlankState.objects.get(pk=blank.pk).state == ""
        assert FieldBlankState.objects.get(pk=enabled.pk).state == OptionalKind.ENABLED
    finally:
        with connection.schema_editor() as schema_editor:
            schema_editor.delete_model(FieldBlankState)


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

    with connection.schema_editor() as schema_editor:
        schema_editor.create_model(SqidNode)
    try:
        root = SqidNode.objects.create()
        child = SqidNode.objects.create(parent=root)

        values = dict(SqidNode.objects.values_list("pk", "parent__sqid"))

        assert values[root.pk] is None
        assert values[child.pk] == root.sqid
        assert str(root.sqid).startswith("tst_")
    finally:
        with connection.schema_editor() as schema_editor:
            schema_editor.delete_model(SqidNode)


@pytest.mark.django_db(transaction=True)
def test_sqid_field_canonical_prefix_uses_separator() -> None:
    """Bare declarations still expose public ids as ``prefix_value``."""

    class BarePrefixNode(models.Model):
        """Concrete model used for prefix normalization tests."""

        sqid = SqidField(real_field_name="id", prefix="bare", min_length=8)

        class Meta:
            """Django model options for the test model."""

            app_label = "auth"

    with connection.schema_editor() as schema_editor:
        schema_editor.create_model(BarePrefixNode)
    try:
        node = BarePrefixNode.objects.create()

        assert BarePrefixNode._meta.get_field("sqid").prefix == "bare_"
        assert str(node.sqid).startswith("bare_")
        assert BarePrefixNode.objects.get(sqid=node.sqid) == node
        assert BarePrefixNode.objects.filter(sqid=str(node.sqid).replace("bare_", "bare", 1)).first() is None
    finally:
        with connection.schema_editor() as schema_editor:
            schema_editor.delete_model(BarePrefixNode)


def test_sqid_field_deconstruct_preserves_public_id_contract() -> None:
    """Generated/runtime model state carries sqid prefix and encoder settings."""

    field = SqidField(real_field_name="id", prefix="abc_", min_length=8)
    _, _, _, kwargs = field.deconstruct()

    assert field.prefix == "abc_"
    assert kwargs["prefix"] == "abc_"
    assert kwargs["real_field_name"] == "id"
    assert kwargs["min_length"] == 8


@pytest.mark.django_db(transaction=True)
def test_sqid_mixin_resolves_prefix_from_sqid_prefix_attr() -> None:
    """A model states only ``sqid_prefix``; the shared field reads it."""

    class PrefixedThing(SqidMixin):
        """Concrete model declaring only its public-id prefix."""

        sqid_prefix = "abc_"

        class Meta:
            """Django model options for the test model."""

            app_label = "auth"

    with connection.schema_editor() as schema_editor:
        schema_editor.create_model(PrefixedThing)
    try:
        thing = PrefixedThing.objects.create()

        assert PrefixedThing._meta.get_field("sqid").prefix == "abc_"
        assert str(thing.sqid).startswith("abc_")
    finally:
        with connection.schema_editor() as schema_editor:
            schema_editor.delete_model(PrefixedThing)


def test_sqid_field_rejects_non_string_prefix() -> None:
    """A non-string ``sqid_prefix`` fails loudly at class definition."""

    with pytest.raises(ImproperlyConfigured, match="sqid_prefix must be a str"):

        class BadPrefixThing(SqidMixin):
            """Model misconfiguring its public-id prefix."""

            sqid_prefix = 5  # type: ignore[assignment]

            class Meta:
                """Django model options for the test model."""

                app_label = "auth"
