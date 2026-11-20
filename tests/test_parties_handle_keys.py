"""A handle's comparison key follows its normalization rule: the repair saves again the keys a rule change left."""

from __future__ import annotations

from io import StringIO
from typing import Any

import pytest
from django.apps import apps
from django.core.management import call_command
from django.core.management.base import OutputWrapper
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django_compose.provision import ProvisionContext
from rebac import system_context

import tests.test_parties_circles  # noqa: F401 -- register the fixture model graph before database setup
from angee.base.querysets import query_batch_size
from angee.messaging.testing.models import Handle, Party, Person
from angee.parties.managers import EmailBasis
from tests.conftest import create_user

pytestmark = pytest.mark.django_db


@pytest.fixture
def owner(db: None) -> Any:
    del db
    return create_user("handle-keys-owner")


def _stored_with(owner: Any, value: str, key: str, *, platform: str = "email") -> Any:
    """A handle saved while the rule still gave ``key``: the key it stores is behind the rule as it is now."""

    with system_context(reason="tests.parties.handle_keys"):
        handle = Handle.objects.upsert(platform=platform, value=value, created_by_id=owner.pk)
        Handle._base_manager.filter(pk=handle.pk).update(normalized_value=key)
    return Handle._base_manager.get(pk=handle.pk)


def _row(handle: Any) -> tuple[str, str, Any, int]:
    """A row as the repair may change it: its key, its value, when it was last saved, and how many versions it has."""

    stored = Handle._base_manager.get(pk=handle.pk)
    history: Any = Handle.history
    versions = history.model._base_manager.filter(id=handle.pk).count()
    return stored.normalized_value, stored.value, stored.updated_at, versions


def test_a_handle_stored_with_a_punycode_domain_key_is_found_by_its_unicode_spelling_after_one_run(owner: Any) -> None:
    """The handle's own save writes the key: the value stays as typed, and the row gains one version, once."""

    with system_context(reason="tests.parties.handle_keys"):
        ada = Person.objects.create(display_name="Ada Example", created_by=owner)
        held = Handle.objects.upsert(platform="email", value="ada@xn--bcher-kva.example", created_by_id=owner.pk)
        Party.objects.assert_handle(ada, held, asserted_by=owner)
    stale = _stored_with(owner, "ada@xn--bcher-kva.example", "ada@xn--bcher-kva.example")
    _key, value, _saved, versions = _row(stale)

    def found() -> list[tuple[Any, EmailBasis]]:
        with system_context(reason="tests.parties.handle_keys"):
            holders = Party.objects.people_for_emails(["ada@bücher.example"])
        return [(holder.person.pk, holder.basis) for holder in holders]

    assert found() == []

    assert Handle.objects.renormalize_handles() == (1, 0)

    assert found() == [(ada.pk, EmailBasis.HELD)]
    repaired = _row(stale)
    assert (repaired[0], repaired[1], repaired[3]) == ("ada@bücher.example", value, versions + 1)
    assert Handle.objects.renormalize_handles() == (0, 0)
    assert _row(stale) == repaired


def test_the_repair_stops_at_its_cap_and_the_next_run_takes_the_rest(owner: Any) -> None:
    stale = [_stored_with(owner, f"ada-{index}@xn--bcher-kva.example", "behind") for index in range(3)]

    first = Handle.objects.renormalize_handles(limit=2)
    second = Handle.objects.renormalize_handles(limit=2)

    assert (first, second) == ((2, 1), (1, 0))
    assert [_row(handle)[0] for handle in stale] == [f"ada-{index}@bücher.example" for index in range(3)]


def test_a_key_the_rule_still_gives_is_left_as_it_is_on_every_platform(owner: Any) -> None:
    with system_context(reason="tests.parties.handle_keys"):
        current = [
            Handle.objects.upsert(platform=platform, value=value, created_by_id=owner.pk)
            for platform, value in (
                ("email", "Ada@Example.test"), ("phone", "+1 787 555 0100"), ("matrix", "@Ada:Example.test"),
                ("other", "Ada Example"),
            )
        ]
    before = [_row(handle) for handle in current]

    with CaptureQueriesContext(connection) as queries:
        repaired = Handle.objects.renormalize_handles()

    assert repaired == (0, 0)
    assert [_row(handle) for handle in current] == before
    assert not [query["sql"] for query in queries if query["sql"].startswith("UPDATE")]


def test_the_repair_compares_every_platform_against_its_rule_as_it_is_now(owner: Any) -> None:
    """Whatever the rule change was: a number stored by its digits, a name stored with its capitals."""

    number = _stored_with(owner, "+1 787 555 0100", "17875550100", platform="phone")
    name = _stored_with(owner, "Ada Example", "Ada Example", platform="other")

    assert Handle.objects.renormalize_handles() == (2, 0)

    assert (_row(number)[0], _row(name)[0]) == ("+17875550100", "ada example")


def test_rows_past_one_read_of_the_table_are_reached(owner: Any) -> None:
    """The table is read in key batches, never at once: a stale key after the first batch is still saved."""

    size = query_batch_size(Handle._base_manager.all())
    with system_context(reason="tests.parties.handle_keys"):
        Handle._base_manager.bulk_create(
            Handle(platform="other", value=f"name-{index}", normalized_value=f"name-{index}", created_by=owner)
            for index in range(size)
        )
    last = _stored_with(owner, "ada@xn--bcher-kva.example", "behind")

    assert Handle.objects.renormalize_handles() == (1, 0)

    assert _row(last)[0] == "ada@bücher.example"


def test_an_operator_sees_how_many_keys_were_saved_and_where_a_capped_repair_stopped(owner: Any) -> None:
    for index in range(2):
        _stored_with(owner, f"ada-{index}@xn--bcher-kva.example", "behind")
    output = StringIO()

    call_command("renormalize_handles", limit=1, stdout=output)

    assert output.getvalue().splitlines() == [
        "Saved the comparison key of 1 handle(s) again.",
        "Stopped at the cap of 1 handle(s); 1 more await the next provision or renormalize_handles run.",
    ]


def _provision() -> str:
    """Provision parties as a deployment does; return what the log shows."""

    output = StringIO()
    parties: Any = apps.get_app_config("parties")
    parties.compose_provision(ProvisionContext("default", OutputWrapper(output), OutputWrapper(StringIO())))
    return output.getvalue()


def _provisioned_repair(limit: int | None = None) -> str:
    """The key repair as provisioning calls it, with a cap a test can reach."""

    output = StringIO()
    call_command("renormalize_handles", unless_applied=True, limit=limit, stdout=output)
    return output.getvalue()


def test_provisioning_runs_the_key_repair(owner: Any) -> None:
    """A deployment never depends on someone running the command: a provision saves the keys a rule left behind."""

    stale = _stored_with(owner, "ada@xn--bcher-kva.example", "behind")

    assert "Saved the comparison key of 1 handle(s) again." in _provision()

    assert _row(stale)[0] == "ada@bücher.example"


def test_provisioning_reads_no_handle_while_the_rules_version_is_the_one_last_applied(owner: Any) -> None:
    """The rule changes almost never: once a version is fully applied, a provision asks its record and reads no row."""

    assert "Saved the comparison key of 0 handle(s) again." in _provision()
    assert Handle.objects.normalization_applied()
    untouched = _stored_with(owner, "ada@xn--bcher-kva.example", "behind")
    table = connection.ops.quote_name(Handle._meta.db_table)

    with CaptureQueriesContext(connection) as queries:
        log = _provisioned_repair()

    assert not [query["sql"] for query in queries if table in query["sql"]]
    assert log.strip() == f"Handle keys follow normalization rule version {Handle.NORMALIZATION_VERSION}; nothing read."
    assert "nothing read." in _provision()
    assert _row(untouched)[0] == "behind"


def test_a_bumped_version_is_read_saved_and_recorded_by_the_next_provision(
    owner: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _provision()
    stale = _stored_with(owner, "ada@xn--bcher-kva.example", "behind")
    monkeypatch.setattr(Handle, "NORMALIZATION_VERSION", Handle.NORMALIZATION_VERSION + 1)
    assert not Handle.objects.normalization_applied()

    assert "Saved the comparison key of 1 handle(s) again." in _provision()

    assert (_row(stale)[0], Handle.objects.normalization_applied()) == ("ada@bücher.example", True)
    assert "nothing read." in _provision()


def test_a_capped_run_records_nothing_and_the_next_provision_resumes_and_then_records(owner: Any) -> None:
    stale = [_stored_with(owner, f"ada-{index}@xn--bcher-kva.example", "behind") for index in range(2)]

    first = _provisioned_repair(limit=1)
    applied_after_first = Handle.objects.normalization_applied()
    second = _provisioned_repair(limit=1)

    assert "1 more await the next provision" in first and not applied_after_first
    assert second.splitlines() == ["Saved the comparison key of 1 handle(s) again."]
    assert Handle.objects.normalization_applied()
    assert [_row(handle)[0] for handle in stale] == [f"ada-{index}@bücher.example" for index in range(2)]
    assert "nothing read." in _provisioned_repair(limit=1)


def test_the_command_reads_every_handle_whatever_is_recorded(owner: Any) -> None:
    """An operator's explicit run always scans, and records the version when it leaves nothing awaiting."""

    assert Handle.objects.renormalize_handles() == (0, 0)
    assert Handle.objects.normalization_applied()
    stale = _stored_with(owner, "ada@xn--bcher-kva.example", "behind")
    output = StringIO()

    call_command("renormalize_handles", stdout=output)

    assert output.getvalue().splitlines() == ["Saved the comparison key of 1 handle(s) again."]
    assert (_row(stale)[0], Handle.objects.normalization_applied()) == ("ada@bücher.example", True)


def test_a_database_without_handles_records_the_version_on_its_first_provision(db: None) -> None:
    del db
    assert not Handle._base_manager.exists() and not Handle.objects.normalization_applied()

    _provision()

    assert Handle.objects.normalization_applied()


def test_a_version_applied_before_then_left_and_returned_to_is_read_again(
    owner: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The latest record says which version the stored keys follow: an earlier one of the same number does not."""

    earlier = Handle.NORMALIZATION_VERSION
    _provision()
    monkeypatch.setattr(Handle, "NORMALIZATION_VERSION", earlier + 1)
    _provision()
    monkeypatch.setattr(Handle, "NORMALIZATION_VERSION", earlier)
    stale = _stored_with(owner, "ada@xn--bcher-kva.example", "behind")

    assert not Handle.objects.normalization_applied()
    assert "Saved the comparison key of 1 handle(s) again." in _provision()
    assert _row(stale)[0] == "ada@bücher.example"


NORMALIZATION_SAMPLE: dict[int, tuple[dict[tuple[str, str], str], dict[str, str]]] = {
    1: (
        {
            ("email", "ada@example.test"): "ada@example.test",
            ("email", "  Ada.Example@Example.TEST "): "ada.example@example.test",
            ("email", "A.Lice+News@GMail.Com"): "alice@gmail.com",
            ("email", "Alice.Smith+work@googlemail.com"): "alicesmith@googlemail.com",
            ("email", "jörg@example.test"): "jörg@example.test",
            ("email", "Ada@Bücher.example"): "ada@bücher.example",
            ("email", "ada@XN--BCHER-KVA.example"): "ada@bücher.example",
            ("email", "ada@xn--9.example"): "ada@xn--9.example",
            ("email", "n/a"): "n/a",
            ("imessage", "Ada@Example.test"): "ada@example.test",
            ("imessage", "+1 787 555 0100"): "+17875550100",
            ("phone", "+1 787 555 0100"): "+17875550100",
            ("phone", "+1 (787) 555-0100"): "+17875550100",
            ("phone", "787 555 0100"): "7875550100",
            ("phone", "n/a"): "",
            ("whatsapp", "+420 777 123 456"): "+420777123456",
            ("discord", "  Ada.Example "): "ada.example",
            ("matrix", "@Ada:Example.test"): "@ada:example.test",
            ("signal", "  Ada.Example "): "ada.example",
            ("slack", "  Ada.Example "): "ada.example",
            ("telegram", "  Ada.Example "): "ada.example",
            ("youtube", "  Ada.Example "): "ada.example",
            ("facebook", "  Ada Example "): "ada example",
            ("instagram", "  Ada.Example "): "ada.example",
            ("linkedin", "  Ada-Example "): "ada-example",
            ("twitter", "  @AdaExample "): "@adaexample",
            ("other", "  Ada Example "): "ada example",
        },
        {
            "Example.TEST": "example.test",
            " Bücher.Example ": "bücher.example",
            "xn--bcher-kva.example": "bücher.example",
            "mail.XN--BCHER-KVA.example": "mail.bücher.example",
            "xn--strae-oqa.example": "xn--strae-oqa.example",
            "": "",
        },
    ),
}
"""What the normalization rule returns for a fixed sample, by ``Handle.NORMALIZATION_VERSION``.

The first mapping is ``Handle.normalize_value`` by ``(platform, value)``, with every
platform in it; the second is ``Handle.normalize_email_domain`` by domain. A change of the
rule adds an entry under the new version number; the entries of earlier versions stay as
the record of what they returned.
"""


def test_changing_what_the_normalization_rule_returns_means_bumping_its_version() -> None:
    """Stored keys are saved again only when the version moves, so an output may not change under one number."""

    version = Handle.NORMALIZATION_VERSION
    assert version in NORMALIZATION_SAMPLE, (
        f"Handle.NORMALIZATION_VERSION is {version}: check in what the rule returns for the sample under that number."
    )
    values, domains = NORMALIZATION_SAMPLE[version]
    assert {platform for platform, _value in values} == set(Handle.Platform.values), (
        "The sample names every platform a handle may have: add a value for the platform it lacks."
    )
    returned = (
        {sample: Handle.normalize_value(*sample) for sample in values},
        {domain: Handle.normalize_email_domain(domain) for domain in domains},
    )
    assert returned == (values, domains), (
        f"The normalization rule no longer returns what version {version} pinned. Bump Handle.NORMALIZATION_VERSION "
        "and check in the new outputs under the new number: provisioning saves stored keys again only when it moves."
    )
