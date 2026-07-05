"""Tests for the IAM company of record, its hierarchy, and the scope mixin."""

from __future__ import annotations

from typing import Any

import pytest
from django.apps import apps
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, models
from rebac import (
    RelationshipTuple,
    actor_context,
    system_context,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)

from angee.base.mixins import ArchiveMixin, SqidMixin
from angee.base.models import AngeeModel


def _grant(resource: Any, relation: str, user: Any) -> None:
    """Write one direct relationship tuple for ``user`` on ``resource``."""

    write_relationships(
        [
            RelationshipTuple(
                resource=to_object_ref(resource),
                relation=relation,
                subject=to_subject_ref(user),
            )
        ]
    )


def test_company_composes_data_and_archive_mixins() -> None:
    """Company is an archivable, sqid-bearing runtime data model."""

    from angee.iam.models import Company

    assert Company._meta.abstract is True
    assert Company.is_runtime_model() is True
    assert Company.sqid_prefix == "com_"
    assert issubclass(Company, SqidMixin)
    assert issubclass(Company, ArchiveMixin)

    field_names = {field.name for field in Company._meta.get_fields()}
    assert {"name", "parent", "sqid", "is_archived"} <= field_names

    parent = Company._meta.get_field("parent")
    assert isinstance(parent, models.ForeignKey)
    assert parent.null is True
    assert parent.remote_field.on_delete is models.PROTECT


def test_company_scoped_mixin_contributes_company_fk() -> None:
    """CompanyScopedMixin contributes a PROTECTed company FK to iam.Company."""

    from angee.iam.models import CompanyScopedMixin

    class _Scoped(CompanyScopedMixin, AngeeModel):
        class Meta:
            abstract = True
            app_label = "iam"

    field = _Scoped._meta.get_field("company")
    assert isinstance(field, models.ForeignKey)
    assert field.null is False
    assert field.remote_field.on_delete is models.PROTECT
    assert str(field.remote_field.model).lower() == "iam.company"


def test_parties_extension_contributes_party_link() -> None:
    """The parties addon extends iam.Company with a nullable party OneToOne."""

    from angee.parties.models import CompanyParties

    assert CompanyParties.get_extension_target() == "iam.company"

    field = CompanyParties._meta.get_field("party")
    assert isinstance(field, models.OneToOneField)
    assert field.null is True
    assert field.remote_field.on_delete is models.PROTECT
    assert field.remote_field.related_name == "company_of_record"
    assert str(field.remote_field.model).lower() == "parties.party"


@pytest.mark.django_db
def test_company_default_returns_first_unarchived_by_pk() -> None:
    """``default()`` returns the sole unarchived company, else the first by pk."""

    company_model = apps.get_model("iam", "Company")
    with system_context(reason="test company default"):
        assert company_model.objects.default() is None
        first = company_model.objects.create(name="First")
        second = company_model.objects.create(name="Second")

        assert company_model.objects.default() == first

        first.is_archived = True
        first.save(update_fields=["is_archived"])
        assert company_model.objects.default() == second


@pytest.mark.django_db
def test_parent_company_member_reaches_subsidiary(django_user_model: Any) -> None:
    """A member of an ancestor company reaches every descendant scope; others none."""

    call_command("rebac", "sync", verbosity=0)
    company_model = apps.get_model("iam", "Company")
    member = django_user_model.objects.create_user(username="parent-member")
    outsider = django_user_model.objects.create_user(username="outsider")
    with system_context(reason="test company hierarchy"):
        parent = company_model.objects.create(name="Holding")
        child = company_model.objects.create(name="Subsidiary", parent=parent)
    _grant(parent, "direct_member", member)

    with actor_context(member):
        assert company_model.objects.filter(pk=parent.pk).exists()
        assert company_model.objects.filter(pk=child.pk).exists()  # via parent->member

    with actor_context(outsider):
        assert not company_model.objects.filter(pk=child.pk).exists()


@pytest.mark.django_db
def test_company_clean_rejects_self_parent() -> None:
    """A company naming itself as parent fails ``full_clean`` — the write path."""

    company_model = apps.get_model("iam", "Company")
    with system_context(reason="test company self-parent"):
        company = company_model.objects.create(name="Solo")
        company.parent = company
        with pytest.raises(ValidationError):
            company.full_clean()


@pytest.mark.django_db
def test_company_check_constraint_rejects_self_parent() -> None:
    """The DB check constraint refuses a self-parent even on a bare save."""

    company_model = apps.get_model("iam", "Company")
    with system_context(reason="test company self-parent db"):
        company = company_model.objects.create(name="Solo")
        company.parent_id = company.pk
        with pytest.raises(IntegrityError):
            company.save(update_fields=["parent"])


@pytest.mark.django_db
def test_company_clean_rejects_ancestor_cycle() -> None:
    """Re-parenting a company under its own descendant (a cycle) is rejected."""

    company_model = apps.get_model("iam", "Company")
    with system_context(reason="test company cycle"):
        top = company_model.objects.create(name="Top")
        middle = company_model.objects.create(name="Middle", parent=top)
        bottom = company_model.objects.create(name="Bottom", parent=middle)
        # Close the loop Top -> Bottom -> Middle -> Top.
        top.parent = bottom
        with pytest.raises(ValidationError):
            top.full_clean()


@pytest.mark.django_db
def test_company_scoped_role_grant_is_isolated_to_that_company(django_user_model: Any) -> None:
    """A company-scoped role binding grants reach only within that company."""

    call_command("rebac", "sync", verbosity=0)
    company_model = apps.get_model("iam", "Company")
    accountant = django_user_model.objects.create_user(username="accountant-a")
    with system_context(reason="test company role isolation"):
        company_a = company_model.objects.create(name="Company A")
        company_b = company_model.objects.create(name="Company B")
    _grant(company_a, "accountant", accountant)

    with actor_context(accountant):
        assert company_model.objects.filter(pk=company_a.pk).exists()
        assert not company_model.objects.filter(pk=company_b.pk).exists()


@pytest.mark.django_db
def test_company_scoped_save_defaults_company_from_sole_membership(django_user_model: Any) -> None:
    """A single-membership actor's scoped row defaults ``company`` on insert (§3.7)."""

    call_command("rebac", "sync", verbosity=0)
    company_model = apps.get_model("iam", "Company")
    scoped_model = apps.get_model("iam", "CompanyScopedDoc")
    member = django_user_model.objects.create_user(username="sole-member")
    with system_context(reason="test scoped default setup"):
        company = company_model.objects.create(name="Sole Co")
    _grant(company, "direct_member", member)

    with actor_context(member):
        doc = scoped_model.objects.create(title="Defaulted")

    assert doc.company_id == company.pk


@pytest.mark.django_db
def test_company_scoped_save_rejects_ambiguous_membership(django_user_model: Any) -> None:
    """Several direct memberships make the default ambiguous — raise naming company."""

    call_command("rebac", "sync", verbosity=0)
    company_model = apps.get_model("iam", "Company")
    scoped_model = apps.get_model("iam", "CompanyScopedDoc")
    member = django_user_model.objects.create_user(username="multi-member")
    with system_context(reason="test scoped ambiguous setup"):
        company_a = company_model.objects.create(name="Co A")
        company_b = company_model.objects.create(name="Co B")
    _grant(company_a, "direct_member", member)
    _grant(company_b, "direct_member", member)

    with actor_context(member), pytest.raises(ValidationError) as excinfo:
        scoped_model.objects.create(title="Ambiguous")

    assert "company" in excinfo.value.message_dict


@pytest.mark.django_db
def test_company_scoped_save_rejects_actorless_write() -> None:
    """No acting actor cannot default ``company`` — raise naming the field."""

    scoped_model = apps.get_model("iam", "CompanyScopedDoc")
    with pytest.raises(ValidationError) as excinfo:
        scoped_model.objects.create(title="No actor")

    assert "company" in excinfo.value.message_dict


@pytest.mark.django_db
def test_company_scoped_save_rejects_anonymous_write() -> None:
    """An anonymous actor cannot default ``company`` — raise naming the field."""

    scoped_model = apps.get_model("iam", "CompanyScopedDoc")
    with actor_context(AnonymousUser()), pytest.raises(ValidationError) as excinfo:
        scoped_model.objects.create(title="Anonymous")

    assert "company" in excinfo.value.message_dict
