"""Native model validation keeps every supported query on the operation alias."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.validators import MinValueValidator
from django.db import IntegrityError, connection, connections, models, router, transaction

from angee.base.models import AngeeModel
from tests.test_transitions import TransitionRouter


class ValidationTarget(AngeeModel):
    """Foreign-key target with a native manager for isolated validation checks."""

    objects = models.Manager()

    class Meta:
        app_label = "tests"


class ValidationRecord(AngeeModel):
    """Exercise native field, composite, and declared constraint validation."""

    objects = models.Manager()
    target = models.ForeignKey(ValidationTarget, on_delete=models.CASCADE, validators=[MinValueValidator(1)])
    optional_target = models.ForeignKey(ValidationTarget, on_delete=models.CASCADE, null=True, blank=True)
    code = models.CharField(max_length=20, unique=True)
    slot = models.CharField(max_length=20)
    label = models.CharField(max_length=20)
    amount = models.IntegerField(default=1)

    class Meta:
        app_label = "tests"
        unique_together = (("target", "slot"),)
        constraints = [
            models.UniqueConstraint(fields=("target", "label"), name="test_validation_target_label"),
            models.CheckConstraint(condition=models.Q(amount__gte=0), name="test_validation_amount"),
        ]

    def clean(self) -> None:
        super().clean()
        self.cleaned_using = self._state.db
        if self.label == "invalid":
            raise ValidationError({"label": "Model clean rejected this label."})


class ValidationChild(ValidationRecord):
    """Keep inherited constraints and parent-link field validation native."""

    class Meta:
        app_label = "tests"


@pytest.fixture
def validation_writer() -> Iterator[str]:
    """Make native validation tables visible through a separate connection alias."""

    with connection.schema_editor() as editor:
        editor.create_model(ValidationTarget)
        editor.create_model(ValidationRecord)
    alias = "model_validation_writer"
    connections[alias] = connection.copy(alias=alias)
    try:
        yield alias
    finally:
        connections[alias].close()
        del connections[alias]
        with connection.schema_editor() as editor:
            editor.delete_model(ValidationRecord)
            editor.delete_model(ValidationTarget)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("duplicate", ["code", "slot", "label", "amount"])
def test_unique_and_constraint_queries_use_explicit_writer(
    validation_writer: str, monkeypatch: pytest.MonkeyPatch, duplicate: str
) -> None:
    """Unique, unique_together, FK constraints and checks bypass a conflicting router."""

    target = ValidationTarget.objects.using(validation_writer).create()
    ValidationRecord.objects.using(validation_writer).create(
        target_id=target.pk, code="existing", slot="existing", label="existing"
    )
    candidate = ValidationRecord(target_id=target.pk, code="new", slot="new", label="new")
    setattr(candidate, duplicate, -1 if duplicate == "amount" else "existing")
    candidate._state.db = "default"
    routing = TransitionRouter("default")
    monkeypatch.setattr(router, "routers", [routing])

    def reject_default_query(*args: Any) -> None:
        raise AssertionError("Validation queried the router's conflicting default database.")

    with connection.execute_wrapper(reject_default_query), pytest.raises(ValidationError) as error:
        candidate.full_clean_for_write(using=validation_writer)
    assert ("code" if duplicate == "code" else "__all__") in error.value.message_dict
    assert candidate.cleaned_using == validation_writer
    assert candidate._state.db == "default"
    assert routing.writes == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("target_id, code", [(None, "null"), ("not-an-integer", "invalid"), (-1, "min_value")])
def test_foreign_key_local_validation_and_model_clean_are_retained(
    validation_writer: str, monkeypatch: pytest.MonkeyPatch, target_id: Any, code: str
) -> None:
    """Skipping FK existence preserves conversion, null checks, validators, and clean()."""

    monkeypatch.setattr(router, "routers", [TransitionRouter("default")])
    candidate = ValidationRecord(target_id=target_id, code="new", slot="new", label="invalid")
    with pytest.raises(ValidationError) as error:
        candidate.full_clean_for_write(using=validation_writer, validate_unique=False, validate_constraints=False)
    assert error.value.error_dict["target"][0].code == code
    assert "label" in error.value.message_dict
    assert candidate.cleaned_using == validation_writer


@pytest.mark.django_db(transaction=True)
def test_foreign_key_existence_is_enforced_by_the_selected_database(
    validation_writer: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing target passes deferred existence validation but cannot be persisted."""

    monkeypatch.setattr(router, "routers", [TransitionRouter("default")])
    candidate = ValidationRecord(target_id=999, code="new", slot="new", label="new")
    candidate.full_clean_for_write(using=validation_writer)
    with pytest.raises(IntegrityError), transaction.atomic(using=validation_writer):
        candidate.save(using=validation_writer)


@pytest.mark.django_db(transaction=True)
def test_inherited_constraints_and_native_parent_link_validation(
    validation_writer: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MTI parent links retain native validation and parent's checks use the writer."""

    target = ValidationTarget.objects.using(validation_writer).create()
    monkeypatch.setattr(router, "routers", [TransitionRouter("default")])
    child = ValidationChild(target_id=target.pk, code="new", slot="new", label="new", amount=-1)
    with pytest.raises(ValidationError) as error:
        child.full_clean_for_write(using=validation_writer)
    assert set(error.value.message_dict) == {"__all__"}
    assert "test_validation_amount" in str(error.value)


@pytest.mark.parametrize(
    "frontier", ["unique_for_date", "unique_for_year", "unique_for_month", "unconstrained_fk", "limit_choices_to"]
)
def test_unsupported_validation_fails_closed_before_database_queries(frontier: str) -> None:
    """No unsupported native validator silently chooses an unrelated database."""

    class UnsupportedValidation(AngeeModel):
        when = models.DateField(default=date(2026, 9, 21))
        code = models.CharField(max_length=20, **({frontier: "when"} if frontier.startswith("unique_for_") else {}))
        target = models.ForeignKey(
            ValidationTarget,
            on_delete=models.CASCADE,
            db_constraint=frontier != "unconstrained_fk",
            limit_choices_to={"pk__gt": 0} if frontier == "limit_choices_to" else {},
        )

        class Meta:
            app_label = "tests"
            abstract = True

    # Angee's abstract declarations expose the same validation owner; constructing
    # a concrete subclass keeps each parameter's fields isolated without a table.
    model = type(
        f"UnsupportedValidation{frontier}",
        (UnsupportedValidation,),
        {"__module__": __name__, "Meta": type("Meta", (), {"app_label": "tests"})},
    )
    candidate = model(code="new", target_id=1)
    with pytest.raises(ImproperlyConfigured, match="Django.*alias"):
        candidate.full_clean_for_write(using="unavailable-writer")


@pytest.mark.parametrize(
    "validate_unique, validate_constraints", [(True, True), (True, False), (False, True), (False, False)]
)
@pytest.mark.parametrize("original_alias", [None, "default", "caller-writer"])
def test_default_alias_retains_native_full_clean_contract(
    monkeypatch: pytest.MonkeyPatch,
    validate_unique: bool,
    validate_constraints: bool,
    original_alias: str | None,
) -> None:
    """Default delegates original flags and excludes without changing caller state."""

    calls: list[tuple[str | None, dict[str, Any]]] = []

    def conditional_full_clean(self: ValidationRecord, **kwargs: Any) -> None:
        calls.append((self._state.db, kwargs))
        if kwargs["validate_unique"] or kwargs["validate_constraints"]:
            raise ValidationError("The full_clean override enforces enabled phases.")

    monkeypatch.setattr(ValidationRecord, "full_clean", conditional_full_clean)
    candidate = ValidationRecord(code="new", slot="new", label="new")
    candidate._state.db = original_alias
    options: dict[str, Any] = {
        "exclude": {"target"}, "validate_unique": validate_unique, "validate_constraints": validate_constraints
    }
    if validate_unique or validate_constraints:
        with pytest.raises(ValidationError, match="full_clean override"):
            candidate.full_clean_for_write(using="default", **options)
    else:
        candidate.full_clean_for_write(using="default", **options)
    assert calls == [(original_alias, options)]
    assert calls[0][1]["exclude"] is options["exclude"]
    assert candidate._state.db == original_alias


def test_default_alias_invokes_both_native_validation_phase_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    """Django retains dispatch to consumer uniqueness and constraint validation hooks."""

    calls = []

    def validate_unique(self: ValidationTarget, exclude: Any = None) -> None:
        calls.append(("validate_unique", self._state.db))
        raise ValidationError({"unique_rule": "Consumer unique rule rejected the row."})

    def validate_constraints(self: ValidationTarget, exclude: Any = None) -> None:
        calls.append(("validate_constraints", self._state.db))
        raise ValidationError({"constraint_rule": "Consumer constraint rule rejected the row."})

    monkeypatch.setattr(ValidationTarget, "validate_unique", validate_unique)
    monkeypatch.setattr(ValidationTarget, "validate_constraints", validate_constraints)
    candidate = ValidationTarget()
    candidate._state.db = "caller-writer"
    with pytest.raises(ValidationError) as error:
        candidate.full_clean_for_write(using="default")
    assert set(error.value.message_dict) == {"unique_rule", "constraint_rule"}
    assert calls == [("validate_unique", "caller-writer"), ("validate_constraints", "caller-writer")]
    assert candidate._state.db == "caller-writer"


@pytest.mark.parametrize("original_alias", [None, "default", "operation-writer"])
@pytest.mark.parametrize("failure", [None, ValidationError, RuntimeError])
def test_nondefault_alias_pin_is_restored_after_validation(
    monkeypatch: pytest.MonkeyPatch, original_alias: str | None, failure: type[Exception] | None
) -> None:
    """Successful validation, validation errors and unexpected clean errors restore state."""

    observed = []

    def clean(self: ValidationTarget) -> None:
        observed.append(self._state.db)
        if failure is not None:
            raise failure("Validation failed.")

    monkeypatch.setattr(ValidationTarget, "clean", clean)
    candidate = ValidationTarget()
    candidate._state.db = original_alias
    if failure is None:
        candidate.full_clean_for_write(using="operation-writer")
    else:
        with pytest.raises(failure, match="Validation failed"):
            candidate.full_clean_for_write(using="operation-writer")
    assert observed == ["operation-writer"]
    assert candidate._state.db == original_alias


@pytest.mark.parametrize("hook", ["validate_unique", "validate_constraints"])
@pytest.mark.parametrize("inherited", [False, True])
@pytest.mark.parametrize("enabled", [False, True])
def test_nondefault_validation_phase_overrides_fail_closed(
    monkeypatch: pytest.MonkeyPatch, hook: str, inherited: bool, enabled: bool
) -> None:
    """Direct and inherited native phase overrides cannot be silently bypassed."""

    def unsupported_hook(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("An unsupported alias-less validation hook must not execute.")

    owner = ValidationRecord if inherited else ValidationTarget
    model = ValidationChild if inherited else ValidationTarget
    monkeypatch.setattr(owner, hook, unsupported_hook)
    candidate = model()
    candidate._state.db = "caller-writer"
    with pytest.raises(ImproperlyConfigured, match=f"overridden {hook}.*operation-writer.*no alias"):
        candidate.full_clean_for_write(
            using="operation-writer", validate_unique=enabled, validate_constraints=enabled
        )
    assert candidate._state.db == "caller-writer"


@pytest.mark.django_db(transaction=True)
def test_default_alias_preserves_native_validation_errors(validation_writer: str) -> None:
    """Default FK, local, unique, composite, and declared checks retain their errors."""

    del validation_writer
    target = ValidationTarget.objects.create()
    ValidationRecord.objects.create(target_id=target.pk, code="same", slot="same", label="same")
    native = ValidationRecord(target_id=target.pk, code="same", slot="same", label="same", amount=-1)
    routed = ValidationRecord(target_id=target.pk, code="same", slot="same", label="same", amount=-1)
    with pytest.raises(ValidationError) as native_error:
        native.full_clean()
    with pytest.raises(ValidationError) as routed_error:
        routed.full_clean_for_write(using="default")
    assert routed_error.value.message_dict == native_error.value.message_dict
