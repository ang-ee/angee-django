"""A handle's comparison key carries the version of the rule that gave it: the repair saves again what is behind."""

from __future__ import annotations

import importlib
import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from io import StringIO
from typing import Any

import pytest
from django.apps import apps
from django.core.management import call_command
from django.core.management.base import OutputWrapper
from django.db import connection
from django.db.models.signals import pre_save
from django.test.utils import CaptureQueriesContext
from django_compose.provision import ProvisionContext
from rebac import system_context

import tests.test_parties_circles  # noqa: F401 -- register the fixture model graph before database setup
from angee.messaging.testing.models import Handle, Party, Person
from angee.parties.managers import EmailBasis, KeyRepair
from tests.conftest import create_user

pytestmark = pytest.mark.django_db


@pytest.fixture
def owner(db: None) -> Any:
    del db
    return create_user("handle-keys-owner")


def _stored_with(owner: Any, value: str, key: str, *, platform: str = "email", version: int = 0) -> Any:
    """A handle as another version of the rule left it: ``key`` stored under ``version``, 0 before the rule had one."""

    with system_context(reason="tests.parties.handle_keys"):
        handle = Handle.objects.upsert(platform=platform, value=value, created_by_id=owner.pk)
        Handle._base_manager.filter(pk=handle.pk).update(normalized_value=key, normalized_version=version)
    return Handle._base_manager.get(pk=handle.pk)


def _row(handle: Any) -> tuple[str, int, str, int]:
    """A row as the repair may change it: its key and the version beside it, its value, and how many versions it has."""

    stored = Handle._base_manager.get(pk=handle.pk)
    history: Any = Handle.history
    versions = history.model._base_manager.filter(id=handle.pk).count()
    return stored.normalized_value, stored.normalized_version, stored.value, versions


def _keyed(handle: Any) -> tuple[str, int]:
    return _row(handle)[:2]


def _behind() -> int:
    return Handle._base_manager.exclude(normalized_version=Handle.NORMALIZATION_VERSION).count()


@contextmanager
def _unsaveable(handle: Any) -> Iterator[None]:
    """While it lasts, a save of ``handle`` raises; a second attempt at it fails the test instead of spinning."""

    attempts = []

    def refuse(sender: Any, instance: Any, **kwargs: Any) -> None:
        del sender, kwargs
        if instance.pk != handle.pk:
            return
        if attempts:
            pytest.fail("One run attempted the same row twice.")
        attempts.append(instance.pk)
        raise RuntimeError("This row cannot be saved.")

    pre_save.connect(refuse, sender=Handle)
    try:
        yield
    finally:
        pre_save.disconnect(refuse, sender=Handle)


def test_every_write_of_the_key_writes_the_rules_version_beside_it(owner: Any) -> None:
    """The handle's save is the one writer of the key: an insert, a changed value, an upsert that reads it again."""

    current = Handle.NORMALIZATION_VERSION
    with system_context(reason="tests.parties.handle_keys"):
        made = Handle._base_manager.create(platform="email", value="Ada@Example.test", created_by=owner)
    assert _keyed(made) == ("ada@example.test", current)

    renamed = _stored_with(owner, "bo@example.test", "behind")
    refreshed = _stored_with(owner, "cy@example.test", "behind")
    rewritten = _stored_with(owner, "di@example.test", "behind")
    labelled = _stored_with(owner, "ed@example.test", "behind")
    with system_context(reason="tests.parties.handle_keys"):
        renamed.value = "Bo@Example.test"
        renamed.save(update_fields=["value"])
        Handle.objects.upsert(platform="email", value="cy@example.test")
        Handle.objects.reidentify(rewritten, expected_value="di@example.test", value="Di@Example.test")
        labelled.label = "work"
        labelled.save(update_fields=["label"])

    assert _keyed(renamed) == ("bo@example.test", current)
    assert _keyed(refreshed) == ("cy@example.test", current)
    assert _keyed(rewritten) == ("di@example.test", current)
    # A save that names neither the address nor its key leaves both key columns for the repair.
    assert _keyed(labelled) == ("behind", 0)
    with pytest.raises(TypeError, match="maintained by Handle.save"):
        Handle.objects.upsert(platform="email", value="fay@example.test", normalized_version=current)


def test_a_handle_stored_with_a_punycode_domain_key_is_found_by_its_unicode_spelling_after_one_run(owner: Any) -> None:
    """A row stored before the rule had a version is selected: its key is saved again, its value stays as typed."""

    with system_context(reason="tests.parties.handle_keys"):
        ada = Person.objects.create(display_name="Ada Example", created_by=owner)
        held = Handle.objects.upsert(platform="email", value="ada@xn--bcher-kva.example", created_by_id=owner.pk)
        Party.objects.assert_handle(ada, held, asserted_by=owner)
    stale = _stored_with(owner, "ada@xn--bcher-kva.example", "ada@xn--bcher-kva.example")
    _key, _version, value, versions = _row(stale)

    def found() -> list[tuple[Any, EmailBasis]]:
        with system_context(reason="tests.parties.handle_keys"):
            holders = Party.objects.people_for_emails(["ada@bücher.example"])
        return [(holder.person.pk, holder.basis) for holder in holders]

    assert found() == []

    assert Handle.objects.renormalize_handles() == KeyRepair(saved=1)

    assert found() == [(ada.pk, EmailBasis.HELD)]
    assert _row(stale) == ("ada@bücher.example", Handle.NORMALIZATION_VERSION, value, versions + 1)
    assert Handle.objects.renormalize_handles() == KeyRepair(saved=0)
    assert _row(stale) == ("ada@bücher.example", Handle.NORMALIZATION_VERSION, value, versions + 1)


def test_a_run_given_a_cap_stops_at_it_and_the_next_run_takes_the_rest(owner: Any) -> None:
    """An operator's share of the backlog: the cap is on the rows the run saves, and what it left is counted."""

    stale = [_stored_with(owner, f"ada-{index}@xn--bcher-kva.example", "behind") for index in range(3)]

    first = Handle.objects.renormalize_handles(limit=2)
    second = Handle.objects.renormalize_handles(limit=2)

    assert (first, second) == (KeyRepair(saved=2, remaining=1), KeyRepair(saved=1))
    assert [_keyed(handle) for handle in stale] == [
        (f"ada-{index}@bücher.example", Handle.NORMALIZATION_VERSION) for index in range(3)
    ]


def test_a_row_keyed_under_the_rules_version_is_left_as_it_is_on_every_platform(owner: Any) -> None:
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

    assert repaired == KeyRepair(saved=0)
    assert [_row(handle) for handle in current] == before
    assert not [query["sql"] for query in queries if query["sql"].startswith("UPDATE")]


def test_with_no_row_behind_the_repair_reads_one_indexed_query_and_loads_no_row(owner: Any) -> None:
    """What a provision costs once the rows follow the rule: the selection by the version's index, nothing else."""

    with system_context(reason="tests.parties.handle_keys"):
        for index in range(3):
            Handle.objects.upsert(platform="email", value=f"ada-{index}@example.test", created_by_id=owner.pk)
    table = connection.ops.quote_name(Handle._meta.db_table)

    with CaptureQueriesContext(connection) as queries:
        assert Handle.objects.renormalize_handles() == KeyRepair(saved=0)

    (selection,) = [query["sql"] for query in queries if table in query["sql"]]
    assert re.match(rf'SELECT {re.escape(table)}\."id"( AS "pk")? FROM {re.escape(table)} WHERE ', selection)
    assert '"normalized_version" <' in selection and '"normalized_version" >' in selection


def test_a_row_whose_key_the_rule_still_gives_has_its_version_written_and_leaves_the_selection(owner: Any) -> None:
    """Both key columns are written together: the key again, unchanged, beside the version it now carries."""

    unchanged = _stored_with(owner, "ada@example.test", "ada@example.test")
    assert _keyed(unchanged) == ("ada@example.test", 0)

    assert Handle.objects.renormalize_handles() == KeyRepair(saved=1)

    assert _keyed(unchanged) == ("ada@example.test", Handle.NORMALIZATION_VERSION)
    assert Handle.objects.renormalize_handles() == KeyRepair(saved=0)


def test_the_repair_keys_every_platform_by_its_rule_as_it_is_now(owner: Any) -> None:
    """Whatever the rule change was: a number stored by its digits, a name stored with its capitals."""

    number = _stored_with(owner, "+1 787 555 0100", "17875550100", platform="phone")
    name = _stored_with(owner, "Ada Example", "Ada Example", platform="other")

    assert Handle.objects.renormalize_handles() == KeyRepair(saved=2)

    assert (_keyed(number), _keyed(name)) == (
        ("+17875550100", Handle.NORMALIZATION_VERSION), ("ada example", Handle.NORMALIZATION_VERSION),
    )


def test_a_row_stamped_by_a_later_version_is_selected_and_keyed_by_the_rule_as_it_is(owner: Any) -> None:
    """A rollback: what newer code wrote carries its version, so the older code's rule keys it again."""

    later = _stored_with(
        owner, "ada@xn--bcher-kva.example", "a key of the later rule", version=Handle.NORMALIZATION_VERSION + 1,
    )

    assert Handle.objects.renormalize_handles() == KeyRepair(saved=1)

    assert _keyed(later) == ("ada@bücher.example", Handle.NORMALIZATION_VERSION)


def test_a_bumped_version_selects_every_row_keyed_before_it(owner: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    with system_context(reason="tests.parties.handle_keys"):
        rows = [
            Handle.objects.upsert(platform="email", value=f"ada-{index}@example.test", created_by_id=owner.pk)
            for index in range(2)
        ]
    assert Handle.objects.renormalize_handles() == KeyRepair(saved=0)
    bumped = Handle.NORMALIZATION_VERSION + 1
    monkeypatch.setattr(Handle, "NORMALIZATION_VERSION", bumped)

    assert Handle.objects.renormalize_handles() == KeyRepair(saved=2)

    assert [_keyed(handle) for handle in rows] == [(f"ada-{index}@example.test", bumped) for index in range(2)]


def test_a_run_without_a_cap_saves_batch_after_batch_until_no_row_is_behind(owner: Any) -> None:
    """The whole backlog in one run: a row left with a stale key is one the lookups that read keys would miss."""

    count = Handle.objects.REPAIR_LIMIT + 1
    with system_context(reason="tests.parties.handle_keys"):
        Handle._base_manager.bulk_create(
            Handle(platform="other", value=f"name-{index}", normalized_value=f"name-{index}", created_by=owner)
            for index in range(count)
        )
    assert _behind() == count

    assert Handle.objects.renormalize_handles() == KeyRepair(saved=count)

    assert _behind() == 0


def test_a_row_whose_save_raises_is_counted_and_left_behind_while_every_other_row_is_saved(
    owner: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """One row never stops the backlog and is never selected again in the run; the next run takes it."""

    monkeypatch.setattr(Handle.objects, "REPAIR_LIMIT", 2)
    stale = [_stored_with(owner, f"ada-{index}@xn--bcher-kva.example", "behind") for index in range(5)]
    refused = stale[2]
    before = _row(refused)

    with _unsaveable(refused), caplog.at_level(logging.ERROR, logger="angee.parties.managers"):
        repair = Handle.objects.renormalize_handles()

    assert repair == KeyRepair(saved=4, failed=1, remaining=1)
    assert _row(refused) == before
    assert [_keyed(handle) for handle in stale if handle.pk != refused.pk] == [
        (f"ada-{index}@bücher.example", Handle.NORMALIZATION_VERSION) for index in (0, 1, 3, 4)
    ]
    (failure,) = caplog.records
    assert failure.getMessage() == f"The comparison key of handle {refused.pk} could not be saved again."
    assert failure.exc_info is not None and str(failure.exc_info[1]) == "This row cannot be saved."

    assert Handle.objects.renormalize_handles() == KeyRepair(saved=1)
    assert _keyed(refused) == ("ada-2@bücher.example", Handle.NORMALIZATION_VERSION)


def test_a_capped_run_passes_a_row_whose_save_raises_and_still_saves_its_share(
    owner: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cap counts saved rows, so a row that cannot be saved neither uses it up nor holds the run on itself."""

    monkeypatch.setattr(Handle.objects, "REPAIR_LIMIT", 2)
    stale = [_stored_with(owner, f"ada-{index}@xn--bcher-kva.example", "behind") for index in range(4)]

    with _unsaveable(stale[0]):
        repair = Handle.objects.renormalize_handles(limit=2)

    assert repair == KeyRepair(saved=2, failed=1, remaining=2)
    assert [_keyed(handle)[1] for handle in stale] == [0, Handle.NORMALIZATION_VERSION, Handle.NORMALIZATION_VERSION, 0]


def test_an_operator_who_asks_for_a_share_sees_where_the_cap_stopped_the_run(owner: Any) -> None:
    for index in range(2):
        _stored_with(owner, f"ada-{index}@xn--bcher-kva.example", "behind")

    def run() -> list[str]:
        output = StringIO()
        call_command("renormalize_handles", limit=1, stdout=output)
        return output.getvalue().splitlines()

    assert run() == [
        "Saved the comparison key of 1 handle(s) again.",
        "Stopped at the cap of 1 handle(s); 1 more await the next provision or renormalize_handles run.",
    ]
    # The cap line is for rows the cap left: a capped run that reached the end of the backlog has none.
    assert run() == ["Saved the comparison key of 1 handle(s) again."]


def test_without_a_cap_the_command_saves_every_batch_and_never_prints_the_cap_line(
    owner: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Handle.objects, "REPAIR_LIMIT", 2)
    for index in range(5):
        _stored_with(owner, f"ada-{index}@xn--bcher-kva.example", "behind")
    output, errors = StringIO(), StringIO()

    call_command("renormalize_handles", stdout=output, stderr=errors)

    assert output.getvalue().splitlines() == ["Saved the comparison key of 5 handle(s) again."]
    assert errors.getvalue() == ""
    assert _behind() == 0


def test_an_operator_sees_how_many_rows_could_not_be_saved(owner: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Rows that failed are reported apart from rows a cap left: without a cap there is no cap line for them."""

    monkeypatch.setattr(Handle.objects, "REPAIR_LIMIT", 2)
    stale = [_stored_with(owner, f"ada-{index}@xn--bcher-kva.example", "behind") for index in range(3)]
    output, errors = StringIO(), StringIO()

    with _unsaveable(stale[1]):
        call_command("renormalize_handles", stdout=output, stderr=errors)

    assert output.getvalue().splitlines() == ["Saved the comparison key of 2 handle(s) again."]
    assert errors.getvalue().splitlines() == [
        "The comparison key of 1 handle(s) could not be saved; the log has each error, "
        "and the next provision or renormalize_handles run tries them again.",
    ]


def test_every_provision_saves_the_whole_backlog(owner: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """A deployment never depends on someone running the command, and a provision with nothing behind says so."""

    monkeypatch.setattr(Handle.objects, "REPAIR_LIMIT", 2)
    stale = [_stored_with(owner, f"ada-{index}@xn--bcher-kva.example", "behind") for index in range(5)]
    parties: Any = apps.get_app_config("parties")

    def provision() -> str:
        output = StringIO()
        parties.compose_provision(ProvisionContext("default", OutputWrapper(output), OutputWrapper(StringIO())))
        return output.getvalue()

    first = provision()

    assert "Saved the comparison key of 5 handle(s) again." in first
    assert "renormalize_handles run" not in first
    assert [_keyed(handle) for handle in stale] == [
        (f"ada-{index}@bücher.example", Handle.NORMALIZATION_VERSION) for index in range(5)
    ]
    assert "Saved the comparison key of 0 handle(s) again." in provision()


def test_the_keys_version_is_no_field_of_the_handle_a_reader_sees() -> None:
    """Bookkeeping of the key: the handle's GraphQL type names its fields one by one, and does not name this one."""

    # Imported here, after the concrete test models are registered: the source schema resolves them on import.
    schema = importlib.import_module("angee.parties.schema")
    shown = {field.python_name for field in schema.HandleType.__strawberry_definition__.fields}

    assert "normalized_value" in shown and "normalized_version" not in shown


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
