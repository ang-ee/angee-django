"""Uniqueness and constraint checks compare a row with every row only when its save may proceed."""

from __future__ import annotations

from typing import Any

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import models
from rebac import actor_context, system_context
from rebac.backends import reset_backend
from rebac.errors import MissingActorError, PermissionDenied
from rebac.models import PermissionAuditEvent
from rebac.schema import parse_zed
from rebac.testing import install_schema

from angee.base.models.base import AngeeModel
from angee.base.models.ownership import OwnerMixin


class GuardedUniqueRow(AngeeModel):
    """A unique column only its keepers read and write; its owner writes the rest of the row."""

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    email = models.CharField(max_length=80, unique=True)
    code = models.CharField(max_length=40, unique=True, null=True, blank=True)
    title = models.CharField(max_length=40, default="row")

    class Meta:
        app_label = "scopedemo"
        rebac_resource_type = "scopedemo/guarded_unique_row"


class OwnedUniqueRow(OwnerMixin, AngeeModel):
    """Owner-gated creation; an insert leaves the owner to its default."""

    code = models.CharField(max_length=40, unique=True)

    class Meta:
        app_label = "scopedemo"
        rebac_resource_type = "scopedemo/owned_unique_row"


class PlainRow(AngeeModel):
    """No rule that reads other rows."""

    title = models.CharField(max_length=40, default="row")

    class Meta:
        app_label = "scopedemo"
        rebac_resource_type = "scopedemo/plain_row"


SCHEMA = """
definition auth/user {}
definition auth/group {
    relation member: auth/user
}
definition scopedemo/guarded_unique_row {
    relation owner: auth/user // rebac:field=owner
    relation keeper: auth/user
    permission read = owner + keeper
    permission create = owner
    permission write = owner + keeper
    permission delete = owner
    permission read__email = keeper
    permission write__email = keeper
    permission read__code = owner + keeper
    permission write__code = keeper
}
definition scopedemo/owned_unique_row {
    relation owner: auth/user // rebac:field=owner
    permission read = owner
    permission create = owner
    permission write = owner
    permission delete = owner
    permission transfer = owner
    permission write__owner = transfer
}
definition scopedemo/plain_row {
    permission read = authenticated
    permission create = authenticated
    permission write = authenticated
    permission delete = authenticated
}
"""


@pytest.fixture
def rows(db: Any) -> Any:
    install_schema(parse_zed(SCHEMA))
    users = get_user_model().objects
    alice, bob = users.create_user(username="scope-alice"), users.create_user(username="scope-bob")
    with system_context(reason="test constraint scope seed"):
        mine = GuardedUniqueRow.objects.create(owner=alice, email="alice@example.com", code="A1")
        GuardedUniqueRow.objects.create(owner=bob, email="bob@example.com", code="B1")
    try:
        yield type("Rows", (), {"alice": alice, "mine": mine})
    finally:
        reset_backend()


def _bypass_audits() -> int:
    return PermissionAuditEvent.objects.filter(kind=PermissionAuditEvent.KIND_SUDO_BYPASS).count()


def test_an_update_never_learns_whether_a_value_it_may_not_read_is_taken(rows: Any) -> None:
    """The owner writes the row but neither reads nor writes its email: the clean stays silent,
    and the save leaves the email it redacted unwritten."""

    with actor_context(rows.alice):
        row = GuardedUniqueRow.objects.get(pk=rows.mine.pk)
        row.email = "bob@example.com"
        row.full_clean()
        row.save()
    with system_context(reason="test constraint scope read back"):
        assert GuardedUniqueRow.objects.get(pk=rows.mine.pk).email == "alice@example.com"


def test_an_update_its_save_refuses_never_learns_whether_a_value_is_taken(rows: Any) -> None:
    """The owner reads the code but may not write it: the clean stays silent and the save refuses."""

    with actor_context(rows.alice):
        row = GuardedUniqueRow.objects.get(pk=rows.mine.pk)
        row.code = "B1"
        # The email it may not read loaded redacted (None): keep it out of field cleaning.
        row.full_clean(exclude=["email"])
        with pytest.raises(PermissionDenied):
            row.save()


def test_a_new_row_its_save_would_insert_is_told_a_value_is_taken(rows: Any) -> None:
    with actor_context(rows.alice), pytest.raises(ValidationError) as error:
        GuardedUniqueRow(owner=rows.alice, email="bob@example.com").full_clean()
    assert set(error.value.message_dict) == {"email"}


def test_a_candidate_the_check_cannot_evaluate_keeps_its_field_errors(rows: Any) -> None:
    with actor_context(rows.alice), pytest.raises(ValidationError) as error:
        GuardedUniqueRow(owner_id=999_999, email="bob@example.com").full_clean()
    assert set(error.value.message_dict) == {"owner"}


def test_a_system_context_or_an_instance_bypass_checks_every_row(rows: Any) -> None:
    with system_context(reason="test constraint scope ambient"):
        before = _bypass_audits()
        with pytest.raises(ValidationError):
            GuardedUniqueRow(owner=rows.alice, email="bob@example.com").full_clean()
        # Already elevated: no further bypass.
        assert _bypass_audits() == before
    with actor_context(rows.alice), pytest.raises(ValidationError):
        GuardedUniqueRow(owner=rows.alice, email="bob@example.com").sudo(reason="test instance bypass").full_clean()


def test_without_an_actor_the_checks_run_as_they_are_and_strict_mode_refuses_them(rows: Any) -> None:
    with pytest.raises(MissingActorError):
        GuardedUniqueRow(owner=rows.alice, email="bob@example.com").full_clean()


def test_only_a_rule_that_reads_other_rows_elevates_and_once_per_check(rows: Any) -> None:
    with actor_context(rows.alice):
        before = _bypass_audits()
        PlainRow(title="plain").full_clean()
        assert _bypass_audits() == before
        GuardedUniqueRow(owner=rows.alice, email="new@example.com").full_clean()
        # validate_unique elevates; the model declares no Meta constraints.
        assert _bypass_audits() == before + 1


def test_an_insert_left_to_its_owner_default_validates_as_it_will_be_saved(rows: Any) -> None:
    """The create decision and the checks see the owner the save applies, then the default is withdrawn."""

    with system_context(reason="test owner default seed"):
        OwnedUniqueRow.objects.create(owner=rows.alice, code="taken")
    with actor_context(rows.alice):
        candidate = OwnedUniqueRow(code="taken")
        with pytest.raises(ValidationError) as error:
            candidate.full_clean()
        assert set(error.value.message_dict) == {"code"}
        assert candidate.owner_id is None
        fresh = OwnedUniqueRow(code="free")
        fresh.full_clean()
        fresh.save()
    assert fresh.owner_id == rows.alice.pk
