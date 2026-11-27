"""An organization's domain key: its domain in the domain-spelling rule's one spelling, beside the domain as typed.

``Organization.domain_key`` is ``Handle.normalize_email_domain(domain)``, the rule a handle's keys read: every save
of the domain writes it, ``Organization.domain_filter`` finds an organization by any spelling of its domain the rule
joins, history versions it beside the domain, and the provision repair
(``OrganizationManager.renormalize_domains``) keys what a save did not.
"""

from __future__ import annotations

import logging
import unicodedata
from io import StringIO
from types import SimpleNamespace
from typing import Any

import pytest
from django.apps import apps
from django.core.management import call_command
from django.core.management.base import OutputWrapper
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django_compose.provision import ProvisionContext
from rebac import actor_context, system_context

import tests.test_parties_circles  # noqa: F401 -- register the fixture model graph before database setup
from angee.integrate.states import StreamKind
from angee.integrate.testing.models import RecordLink
from angee.messaging.testing.models import Directory, Organization, Party
from angee.parties import managers
from angee.parties.backends import ParsedOrganization
from angee.parties.managers import KeyRepair, KeyRepairOutcome, repair_keys
from angee.provenance.context import changeset
from tests.conftest import create_platform_admin, create_user, current_stream, make_integration

pytestmark = pytest.mark.django_db

NFD = unicodedata.normalize("NFD", "bücher.example")
FULL_WIDTH = "\N{FULLWIDTH LATIN SMALL LETTER E}xample.test"

# The lookup reads the domain-spelling rule on both sides; the rule's own spellings are pinned with the handle's keys.
ONE_DOMAIN = [
    pytest.param("straße.example", "strasse.example", id="sharp-s-asked-ss"),
    pytest.param("xn--bcher-kva.example", NFD, id="punycode-asked-nfd"),
    pytest.param(" Example.TEST ", FULL_WIDTH, id="padded-upper-asked-full-width"),
]


@pytest.fixture
def owner(db: None) -> Any:
    del db
    return create_user("organization-domains-owner")


def _organization(owner: Any, domain: str, name: str = "Bücher") -> Any:
    with system_context(reason="tests.parties.organization_domains"):
        return Organization._base_manager.create(display_name=name, domain=domain, created_by=owner)


def _found(domain: str) -> set[Any]:
    with system_context(reason="tests.parties.organization_domains"):
        return set(Organization._base_manager.filter(Organization.domain_filter(domain)).values_list("pk", flat=True))


def _versions(organizations: list[Any]) -> int:
    with system_context(reason="tests.parties.organization_domains"):
        rows = Organization._base_manager.filter(pk__in=[row.pk for row in organizations])
        return sum(row.history.count() for row in rows)


def _stored(organization: Any) -> tuple[str, str]:
    with system_context(reason="tests.parties.organization_domains"):
        row = Organization._base_manager.get(pk=organization.pk)
        return row.domain, row.domain_key


@pytest.mark.parametrize(("stored", "asked"), ONE_DOMAIN)
def test_an_organization_is_found_by_every_spelling_of_its_domain_the_rule_joins(
    owner: Any, stored: str, asked: str,
) -> None:
    """The domain stays as typed; its key is the rule's spelling, which the lookup reads for the asked spelling too."""

    organization = _organization(owner, stored)

    assert _found(asked) == {organization.pk}
    assert _stored(organization) == (stored, apps.get_model("parties", "Handle").normalize_email_domain(stored))


def test_a_domain_the_rule_keeps_apart_and_no_domain_find_nothing(owner: Any) -> None:
    """IDNA 2008's ``straße`` (``xn--strae-oqa``) is a domain of its own, and an empty domain names no organization."""

    apart = _organization(owner, "xn--strae-oqa.example")
    undomained = _organization(owner, "", name="No domain")

    assert _found("strasse.example") == set()
    assert _found("xn--strae-oqa.example") == {apart.pk}
    assert _found("") == set() and _found("   ") == set()
    assert _stored(undomained) == ("", "")


def test_every_save_of_the_domain_writes_its_key(owner: Any) -> None:
    """A full save and a partial one naming the domain key it; a partial save of another field leaves the key."""

    organization = _organization(owner, "bücher.example")
    with system_context(reason="tests.parties.organization_domains"):
        row = Organization._base_manager.get(pk=organization.pk)
        row.domain = "Straße.example"
        row.save(update_fields=["domain", "updated_at"])
        assert _stored(row) == ("Straße.example", "strasse.example")
        row.domain = NFD
        row.save()
        assert _stored(row) == (NFD, "bücher.example")
        row.display_name = "Renamed"
        with CaptureQueriesContext(connection) as queries:
            row.save(update_fields=["display_name", "updated_at"])
    updates = [query["sql"] for query in queries if query["sql"].startswith("UPDATE ")]
    assert updates and not any("domain_key" in sql for sql in updates)
    assert _found("bücher.example") == {organization.pk}


@pytest.fixture
def source(db: None) -> SimpleNamespace:
    """A directory with an organizations stream."""

    del db
    with system_context(reason="tests.parties.organization_domains.seed"):
        directory = make_integration("organization-domains-source", model=Directory)
        return SimpleNamespace(
            owner=directory.owner,
            organizations=current_stream(directory, "organizations", kind=StreamKind.RECORD_REPLICA),
        )


def test_a_domain_a_directory_states_is_keyed_as_it_lands(source: SimpleNamespace) -> None:
    """The legal identity a source states reaches the organization through its statement, which saves the domain."""

    parsed = ParsedOrganization(display_name="Straße GmbH", legal_name="Straße GmbH", domain="straße.example")
    with system_context(reason="tests.parties.organization_domains.ingest"):
        link = RecordLink.objects.observe(source.organizations, "strasse-gmbh")
        organization = Party.objects.ingest_organization(
            parsed, stream=source.organizations, link=link, created_by_id=source.owner.pk,
        )

    assert _stored(organization) == ("straße.example", "strasse.example")
    assert _found("strasse.example") == {organization.pk}


def _repair() -> KeyRepair:
    with system_context(reason="tests.parties.organization_domains.repair"):
        return Organization.objects.renormalize_domains()


def _writes(queries: CaptureQueriesContext) -> list[str]:
    statements = [query["sql"] for query in queries]
    return [sql for sql in statements if sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))]


def _listed() -> list[tuple[Any, Any]]:
    """The organizations in their list order (the model's ``-updated_at`` first), with their ``updated_at``."""

    with system_context(reason="tests.parties.organization_domains"):
        return list(Organization._base_manager.values_list("pk", "updated_at"))


def test_the_repair_keys_organizations_stored_before_the_key_and_a_second_run_writes_nothing(owner: Any) -> None:
    """The state a migration leaves (an empty key), and a domain a queryset update wrote, are keyed in one run.

    A repair is no edit: it writes the key alone, with one version, so no ``updated_at`` and no list order moves.
    A run that finds nothing stale writes nothing at all, not even its elevation's audit row.
    """

    stored = [_organization(owner, domain, name=f"Org {index}") for index, domain in enumerate(
        ("straße.example", NFD, "xn--bcher-kva.example", FULL_WIDTH, "plain.example"),
    )]
    with system_context(reason="tests.parties.organization_domains"):
        # makemigrations adds the column with a database default of "", so every row stored before it is unkeyed.
        Organization._base_manager.filter(pk__in=[row.pk for row in stored[:4]]).update(domain_key="")
        # A queryset update writes the domain and no key.
        Organization._base_manager.filter(pk=stored[4].pk).update(domain="other.example")
        versions = _versions(stored)
    assert _found("strasse.example") == set()
    listed = _listed()

    assert _repair() == KeyRepair(saved=5)

    assert _listed() == listed

    assert _found("strasse.example") == {stored[0].pk}
    assert _found("bücher.example") == {stored[1].pk, stored[2].pk}
    assert _found("example.test") == {stored[3].pk}
    assert _found("other.example") == {stored[4].pk}
    assert _versions(stored) == versions + 5, "each row the repair keys is saved once, with a version"
    with CaptureQueriesContext(connection) as queries:
        # As a provision calls it: the run's own elevation is all the context it needs.
        assert Organization.objects.renormalize_domains() == KeyRepair(saved=0)
    assert _writes(queries) == []
    assert _versions(stored) == versions + 5


def test_a_domain_that_would_read_longer_than_a_domain_name_is_kept_as_typed_by_the_save_and_the_repair(
    owner: Any,
) -> None:
    """A name of ligatures reads past ``Handle.DOMAIN_NAME_LIMIT`` (251 characters typed would read as 3,107): the
    rule keeps it as typed, so its key fits the column on every database, saved or repaired."""

    ligatures = ".".join(["ﷺﷺ"] * 84)
    organization = _organization(owner, ligatures)
    assert _stored(organization) == (ligatures, ligatures)
    with system_context(reason="tests.parties.organization_domains"):
        Organization._base_manager.filter(pk=organization.pk).update(domain_key="")

    assert _repair() == KeyRepair(saved=1)
    assert _stored(organization) == (ligatures, ligatures)
    assert _found(ligatures) == {organization.pk}


def test_a_save_of_the_key_alone_keys_the_domain_the_row_stores(owner: Any) -> None:
    """A partial save naming the key without the domain never keys a domain changed in memory and not saved."""

    organization = _organization(owner, "straße.example")
    with system_context(reason="tests.parties.organization_domains"):
        row = Organization._base_manager.get(pk=organization.pk)
        row.domain = "bücher.example"
        row.save(update_fields=["domain_key"])

    assert _stored(organization) == ("straße.example", "strasse.example")
    assert _found("bücher.example") == set()


def test_the_repair_works_in_batches_and_takes_every_batch(owner: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Each batch is its own transaction; a run without a cap takes every batch, so nothing is left behind."""

    monkeypatch.setattr("angee.parties.managers.REPAIR_BATCH", 2)
    stored = [_organization(owner, f"ab{index}.straße.example", name=f"Org {index}") for index in range(5)]
    with system_context(reason="tests.parties.organization_domains"):
        Organization._base_manager.filter(pk__in=[row.pk for row in stored]).update(domain_key="")

    assert _repair() == KeyRepair(saved=5)
    assert [_stored(row)[1] for row in stored] == [f"ab{index}.strasse.example" for index in range(5)]


def test_an_organization_whose_key_write_raises_is_counted_and_every_other_one_is_keyed(
    owner: Any, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One row never stops the backlog: it is rolled back, logged and counted, and the next run takes it."""

    stored = [_organization(owner, f"row{index}.straße.example", name=f"Org {index}") for index in range(3)]
    with system_context(reason="tests.parties.organization_domains"):
        Organization._base_manager.filter(pk__in=[row.pk for row in stored]).update(domain_key="")

    derived = managers.most_derived_concrete

    def refuse(row: Any) -> Any:
        if row.pk == stored[1].pk:
            raise RuntimeError("This row cannot be saved.")
        return derived(row)

    with monkeypatch.context() as patched, caplog.at_level(logging.ERROR, logger="angee.parties.managers"):
        # The key write's own step, under the row's lock and in its savepoint.
        patched.setattr(managers, "most_derived_concrete", refuse)
        assert _repair() == KeyRepair(saved=2, failed=1, remaining=1)
    (failure,) = caplog.records
    assert failure.getMessage() == f"The keys of organization {stored[1].pk} could not be saved again."
    assert [_stored(row)[1] for row in stored] == ["row0.strasse.example", "", "row2.strasse.example"]
    assert _repair() == KeyRepair(saved=1)


def test_a_capped_walk_over_candidates_is_refused_before_it_reads() -> None:
    """A cap counts the rows behind; candidates with a stale check would make ``remaining`` count every row."""

    with pytest.raises(ValueError, match="capped key repair"):
        repair_keys(
            Organization._base_manager.order_by("pk"), lambda row: KeyRepairOutcome.CURRENT,
            reason="tests.parties.organization_domains", limit=1, read=("domain",), stale=lambda domain: True,
        )


def test_every_provision_keys_the_organizations_behind_and_the_command_says_how_many(owner: Any) -> None:
    """A deployment never depends on someone running the command; the command reports what it keyed."""

    organization = _organization(owner, "straße.example")
    with system_context(reason="tests.parties.organization_domains"):
        Organization._base_manager.filter(pk=organization.pk).update(domain_key="")
    parties: Any = apps.get_app_config("parties")
    output = StringIO()

    parties.compose_provision(ProvisionContext("default", OutputWrapper(output), OutputWrapper(StringIO())))

    assert "Saved the domain key of 1 organization(s) again." in output.getvalue()
    assert _found("strasse.example") == {organization.pk}
    command = StringIO()
    call_command("renormalize_organization_domains", stdout=command)
    assert "Saved the domain key of 0 organization(s) again." in command.getvalue()


def test_reverting_a_change_of_the_domain_restores_the_domain_and_its_key(
    owner: Any, django_capture_on_commit_callbacks: Any,
) -> None:
    """History versions the key beside the domain, and the revert's save keys the domain it restores."""

    organization = _organization(owner, "straße.example")
    reason = "tests.parties.organization_domains.respell"
    with (
        django_capture_on_commit_callbacks(execute=True),
        changeset(fresh=True, change_reason=reason),
        system_context(reason=reason),
    ):
        row = Organization._base_manager.get(pk=organization.pk)
        row.domain = "bücher.example"
        row.save(update_fields=["domain", "updated_at"])
    assert _found("bücher.example") == {organization.pk}
    administrator = create_platform_admin("organization-domains-reverter")
    with system_context(reason="tests.parties.organization_domains.revert"):
        written = row.history.filter(changeset__change_reason=reason).latest("history_id").changeset
    with actor_context(administrator):
        written.with_actor(administrator).revert()

    assert _stored(organization) == ("straße.example", "strasse.example")
    assert (_found("strasse.example"), _found("bücher.example")) == ({organization.pk}, set())


def test_the_domain_key_is_no_field_a_reader_sees_and_history_versions_it() -> None:
    """Bookkeeping of the domain: the GraphQL type names the domain, not its key; history carries both."""

    import importlib

    # Imported here, after the concrete test models are registered: the source schema resolves them on import.
    schema = importlib.import_module("angee.parties.schema")
    shown = {field.python_name for field in schema.OrganizationType.__strawberry_definition__.fields}
    versioned = {field.name for field in Organization.history.model._meta.fields}

    assert "domain" in shown and "domain_key" not in shown
    assert {"domain", "domain_key"} <= versioned
