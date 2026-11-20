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
    versions = Handle.history.model._base_manager.filter(id=handle.pk).count()
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


def test_provisioning_runs_the_key_repair(owner: Any) -> None:
    """A deployment never depends on someone running the command: every provision saves the keys a rule left behind."""

    stale = _stored_with(owner, "ada@xn--bcher-kva.example", "behind")
    output = StringIO()

    apps.get_app_config("parties").compose_provision(
        ProvisionContext("default", OutputWrapper(output), OutputWrapper(StringIO()))
    )

    assert "Saved the comparison key of 1 handle(s) again." in output.getvalue()
    assert _row(stale)[0] == "ada@bücher.example"
