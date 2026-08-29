"""Tests for Angee model field types."""

from __future__ import annotations

import math
from typing import Any

import pytest
from django.conf import settings
from django.core.exceptions import FieldError, ImproperlyConfigured, ValidationError
from django.db import connection, models
from django.db.models import F, Value
from django.db.models.functions import Concat

from angee.base.fields import (
    EncryptedField,
    FractionalRankExhausted,
    FractionalRankField,
    SqidField,
    StateField,
    _derive_fernet,
)
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


@pytest.mark.django_db(transaction=True)
def test_fractional_rank_appends_within_its_unique_context() -> None:
    """An omitted rank appends per context; an explicit rank is honored."""

    class RankedItem(models.Model):
        """Concrete model used for fractional-rank allocation tests."""

        lane = models.CharField(max_length=8)
        rank = FractionalRankField()

        class Meta:
            """Django model options for the test model."""

            app_label = "auth"
            constraints = (
                models.UniqueConstraint(fields=("lane", "rank"), name="ranked_item_lane_rank"),
            )

    with connection.schema_editor() as schema_editor:
        schema_editor.create_model(RankedItem)
    try:
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
    finally:
        with connection.schema_editor() as schema_editor:
            schema_editor.delete_model(RankedItem)


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
            constraints = (
                models.UniqueConstraint(fields=("lane", "rank"), name="ranked_pending_lane_rank"),
            )

    with connection.schema_editor() as schema_editor:
        schema_editor.create_model(RankedPending)
    try:
        instance = RankedPending(lane="a")

        instance.full_clean()
        instance.save()

        assert instance.rank == FractionalRankField.STEP
    finally:
        with connection.schema_editor() as schema_editor:
            schema_editor.delete_model(RankedPending)


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

    with connection.schema_editor() as schema_editor:
        schema_editor.create_model(RankedUnconstrained)
        schema_editor.create_model(RankedOverconstrained)
    try:
        with pytest.raises(ImproperlyConfigured, match="exactly one"):
            RankedUnconstrained.objects.create()
        with pytest.raises(ImproperlyConfigured, match="exactly one"):
            RankedOverconstrained.objects.create(lane="a", tier="x")

        explicit = RankedUnconstrained.objects.create(rank=64.0)
        assert explicit.rank == 64.0
    finally:
        with connection.schema_editor() as schema_editor:
            schema_editor.delete_model(RankedOverconstrained)
            schema_editor.delete_model(RankedUnconstrained)


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


def test_get_rank_between_midpoints_and_edges() -> None:
    """Between-neighbor ranks midpoint, prepend, append, and exhaust."""

    step = FractionalRankField.STEP

    assert FractionalRankField.get_rank_between(None, None) == step
    assert FractionalRankField.get_rank_between(step, None) == step * 2
    assert FractionalRankField.get_rank_between(None, step) == 0.0
    midpoint = FractionalRankField.get_rank_between(step, step * 2)
    assert step < midpoint < step * 2
    with pytest.raises(ValueError, match="strictly less"):
        FractionalRankField.get_rank_between(step * 2, step)
    adjacent = math.nextafter(1.0, 2.0)
    with pytest.raises(FractionalRankExhausted):
        FractionalRankField.get_rank_between(1.0, adjacent)


def test_fractional_rank_rejects_non_finite_values() -> None:
    """NaN and infinity are refused on both coercion boundaries."""

    field = FractionalRankField()

    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValidationError):
            field.to_python(bad)
        with pytest.raises(ValidationError):
            field.get_prep_value(bad)


@pytest.mark.django_db(transaction=True)
def test_fractional_rank_rebalance_rewrites_a_clean_spread() -> None:
    """Rebalance rewrites one context to clean steps and reports the changes."""

    class RankedRebalance(models.Model):
        """Concrete model used for rank rebalance tests."""

        lane = models.CharField(max_length=8)
        rank = FractionalRankField()

        class Meta:
            """Django model options for the test model."""

            app_label = "auth"
            constraints = (
                models.UniqueConstraint(fields=("lane", "rank"), name="ranked_rebalance_lane_rank"),
            )

    with connection.schema_editor() as schema_editor:
        schema_editor.create_model(RankedRebalance)
    try:
        RankedRebalance.objects.create(lane="a", rank=0.25)
        RankedRebalance.objects.create(lane="a", rank=0.5)
        RankedRebalance.objects.create(lane="a", rank=9000.0)
        untouched = RankedRebalance.objects.create(lane="b", rank=7.0)

        field = RankedRebalance._meta.get_field("rank")
        changed = field.rebalance(context={"lane": "a"})

        assert changed == 3
        ranks = list(
            RankedRebalance.objects.filter(lane="a").order_by("rank").values_list("rank", flat=True)
        )
        step = FractionalRankField.STEP
        assert ranks == [step, step * 2, step * 3]
        untouched.refresh_from_db()
        assert untouched.rank == 7.0

        assert field.rebalance(context={"lane": "a"}) == 0

        # Every row shifts up into the rank its successor currently holds, so
        # writing the clean spread directly (no staging pass) trips the unique
        # constraint mid-statement; only the staged rewrite survives.
        for occupied in (step / 2, step, step * 2):
            RankedRebalance.objects.create(lane="c", rank=occupied)
        assert field.rebalance(context={"lane": "c"}) == 3
        crowded = list(
            RankedRebalance.objects.filter(lane="c").order_by("rank").values_list("rank", flat=True)
        )
        assert crowded == [step, step * 2, step * 3]
    finally:
        with connection.schema_editor() as schema_editor:
            schema_editor.delete_model(RankedRebalance)


def test_fractional_rank_rebalance_rejects_traversal_context_keys() -> None:
    """Context keys must be direct model fields, never lookups or the rank."""

    class RankedContextGuard(models.Model):
        """Concrete model used for rebalance-context validation tests."""

        lane = models.CharField(max_length=8)
        rank = FractionalRankField()

        class Meta:
            """Django model options for the test model."""

            app_label = "auth"
            constraints = (
                models.UniqueConstraint(fields=("lane", "rank"), name="ranked_guard_lane_rank"),
            )

    field = RankedContextGuard._meta.get_field("rank")
    for bad_context in ({"lane__icontains": "a"}, {"missing": 1}, {"rank": 1.0}):
        with pytest.raises(ImproperlyConfigured):
            field.rebalance(context=bad_context)


@pytest.mark.django_db(transaction=True)
def test_fractional_rank_rebalance_accepts_a_relation_attname_context() -> None:
    """A relation context key works as the field name or its column attname."""

    class RankOwner(models.Model):
        """Concrete relation target for relation-context rebalance tests."""

        class Meta:
            """Django model options for the test model."""

            app_label = "auth"

    class RankedRelationContext(models.Model):
        """Concrete model used for relation-context rebalance tests."""

        owner = models.ForeignKey(RankOwner, on_delete=models.CASCADE, null=True)
        rank = FractionalRankField()

        class Meta:
            """Django model options for the test model."""

            app_label = "auth"
            constraints = (
                models.UniqueConstraint(fields=("owner", "rank"), name="ranked_rel_owner_rank"),
            )

    with connection.schema_editor() as schema_editor:
        schema_editor.create_model(RankOwner)
        schema_editor.create_model(RankedRelationContext)
    try:
        field = RankedRelationContext._meta.get_field("rank")
        # Django resolves the column attname to the relation field itself, so
        # both spellings address the same exact-match context.
        assert field.rebalance(context={"owner": None}) == 0
        assert field.rebalance(context={"owner_id": None}) == 0
    finally:
        with connection.schema_editor() as schema_editor:
            schema_editor.delete_model(RankedRelationContext)
            schema_editor.delete_model(RankOwner)
