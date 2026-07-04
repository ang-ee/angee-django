"""Tests for the IAM company of record, its hierarchy, and the scope mixin."""

from __future__ import annotations

from typing import Any

import pytest
from django.apps import apps
from django.core.management import call_command
from django.db import models
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
