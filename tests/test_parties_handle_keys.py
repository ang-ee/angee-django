"""A handle's keys carry the version of the rule that gave them: the repair saves again what is behind, and folds two
live handles of one address into the older."""

from __future__ import annotations

import importlib
import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from io import StringIO
from typing import Any
from unittest import mock

import pytest
from django.apps import apps
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import OutputWrapper
from django.db import IntegrityError, connection, transaction
from django.test.utils import CaptureQueriesContext, override_settings
from django.utils import timezone
from django_compose.provision import ProvisionContext
from hypothesis import given, settings
from hypothesis import strategies as st
from rebac import actor_context, system_context

import tests.test_parties_circles  # noqa: F401 -- register the fixture model graph before database setup
from angee.base.models.identity import public_id_of
from angee.messaging.testing.models import Channel, Handle, Message, Party, Person, Reaction, Recipient, Thread
from angee.parties.managers import REPAIR_BATCH, EmailBasis, KeyRepair, ValueOwned
from angee.provenance.context import changeset
from angee.resources.testing.models import Resource
from tests.conftest import create_platform_admin, create_user, make_integration

pytestmark = pytest.mark.django_db


@pytest.fixture
def owner(db: None) -> Any:
    del db
    return create_user("handle-keys-owner")


def _stored_with(
    owner: Any, value: str, key: str, *, platform: str = "email", version: int = 0, external_id: str = "",
) -> Any:
    """A handle as another version of the rule left it: ``key`` stored under ``version``, 0 before the rule had one."""

    with system_context(reason="tests.parties.handle_keys"):
        handle = Handle.objects.upsert(platform=platform, value=value, external_id=external_id, created_by_id=owner.pk)
        # The state a migration leaves: no spelling key, until the repair reaches the row.
        Handle._base_manager.filter(pk=handle.pk).update(
            normalized_value=key, normalized_version=version, spelling_key="",
        )
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
    """While it lasts, writing ``handle``'s keys raises; a second attempt at it fails the test instead of spinning."""

    attempts: list[Any] = []
    derive = Handle.derived_keys

    def refuse(row: Any) -> dict[str, Any]:
        if row.pk != handle.pk:
            return derive(row)
        if attempts:
            pytest.fail("One run attempted the same row twice.")
        attempts.append(row.pk)
        raise RuntimeError("This row cannot be saved.")

    with mock.patch.object(Handle, "derived_keys", refuse):
        yield


def test_every_write_of_the_key_writes_the_rules_version_beside_it(owner: Any) -> None:
    """The handle's save is the one writer of the key: an insert, a value the handle owner's verbs change, an upsert
    that reads it again."""

    current = Handle.NORMALIZATION_VERSION
    with system_context(reason="tests.parties.handle_keys"):
        made = Handle._base_manager.create(platform="email", value="Ada@Example.test", created_by=owner)
    assert _keyed(made) == ("ada@example.test", current)

    renamed = _stored_with(owner, "bo@example.test", "behind", external_id="bo-account")
    refreshed = _stored_with(owner, "cy@example.test", "behind")
    rewritten = _stored_with(owner, "di@example.test", "behind")
    labelled = _stored_with(owner, "ed@example.test", "behind")
    with system_context(reason="tests.parties.handle_keys"):
        # The address behind the source's stable id moved: upsert rewrites the value its row names.
        Handle.objects.upsert(platform="email", value="Bo.Moved@Example.test", external_id="bo-account")
        Handle.objects.upsert(platform="email", value="cy@example.test")
        Handle.objects.reidentify(rewritten, expected_value="di@example.test", value="Di@Example.test")
        labelled.label = "work"
        labelled.save(update_fields=["label"])

    assert _keyed(renamed) == ("bo.moved@example.test", current)
    assert Handle._base_manager.get(pk=renamed.pk).spelling_key == "bo.moved@example.test"
    assert _keyed(rewritten) == ("di@example.test", current)
    # A hit writes no key, and a save that names neither the address nor a key leaves the key columns for the repair.
    assert _keyed(refreshed) == ("behind", 0)
    assert _keyed(labelled) == ("behind", 0)
    for name in ("normalized_version", "spelling_key"):
        with pytest.raises(TypeError, match="maintained by Handle.save"):
            Handle.objects.upsert(platform="email", value="fay@example.test", **{name: current})


def _writes(queries: CaptureQueriesContext) -> list[str]:
    statements = [query["sql"] for query in queries]
    return [sql for sql in statements if sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))]


def test_a_handle_stored_with_a_punycode_domain_key_is_found_by_its_unicode_spelling_after_one_run(owner: Any) -> None:
    """A row stored before the rule had a version is selected: its key is saved again, its value stays as typed.

    A repair is no edit: the keys are written alone, with one version, and ``updated_at`` stays; a run with nothing
    behind writes nothing at all, not even its elevation's audit row.
    """

    with system_context(reason="tests.parties.handle_keys"):
        ada = Person.objects.create(display_name="Ada Example", created_by=owner)
        held = Handle.objects.upsert(platform="email", value="ada@xn--bcher-kva.example", created_by_id=owner.pk)
        Party.objects.assert_handle(ada, held, asserted_by=owner)
    stale = _stored_with(owner, "ada@xn--bcher-kva.example", "ada@xn--bcher-kva.example")
    _key, _version, value, versions = _row(stale)
    updated_at = Handle._base_manager.values_list("updated_at", flat=True).get(pk=stale.pk)

    def found() -> list[tuple[Any, EmailBasis]]:
        with system_context(reason="tests.parties.handle_keys"):
            holders = Party.objects.people_for_emails(["ada@bücher.example"])
        return [(holder.person.pk, holder.basis) for holder in holders]

    assert found() == []

    assert Handle.objects.renormalize_handles() == KeyRepair(saved=1)

    assert found() == [(ada.pk, EmailBasis.HELD)]
    assert _row(stale) == ("ada@bücher.example", Handle.NORMALIZATION_VERSION, value, versions + 1)
    assert Handle._base_manager.values_list("updated_at", flat=True).get(pk=stale.pk) == updated_at
    with CaptureQueriesContext(connection) as queries:
        assert Handle.objects.renormalize_handles() == KeyRepair(saved=0)
    assert _writes(queries) == []
    assert _row(stale) == ("ada@bücher.example", Handle.NORMALIZATION_VERSION, value, versions + 1)


def test_an_address_whose_domain_would_read_longer_than_a_domain_name_keeps_it_as_typed(owner: Any) -> None:
    """A name of ligatures reads past ``Handle.DOMAIN_NAME_LIMIT``: the rule keeps it as typed, so both keys fit
    their columns on every database, as the handle saves and as the repair writes them."""

    ligatures = ".".join(["ﷺﷺ"] * 84)
    with system_context(reason="tests.parties.handle_keys"):
        handle = Handle.objects.upsert(platform="email", value=f"Ada@{ligatures}", created_by_id=owner.pk)
    stored = Handle._base_manager.get(pk=handle.pk)
    assert (stored.spelling_key, stored.normalized_value) == (f"ada@{ligatures}", f"ada@{ligatures}")

    behind = _stored_with(owner, f"bob@{ligatures}", "behind")
    assert Handle.objects.renormalize_handles() == KeyRepair(saved=1)
    assert _keyed(behind) == (f"bob@{ligatures}", Handle.NORMALIZATION_VERSION)


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

    count = REPAIR_BATCH + 1
    with system_context(reason="tests.parties.handle_keys"):
        Handle._base_manager.bulk_create(
            Handle(platform="other", value=f"name-{index}", normalized_value=f"name-{index}", created_by=owner)
            for index in range(count)
        )
    assert _behind() == count

    assert Handle.objects.renormalize_handles() == KeyRepair(saved=count)

    assert _behind() == 0


def test_a_row_whose_key_write_raises_is_counted_and_left_behind_while_every_other_row_is_saved(
    owner: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """One row never stops the backlog and is never selected again in the run; the next run takes it."""

    monkeypatch.setattr("angee.parties.managers.REPAIR_BATCH", 2)
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
    assert failure.getMessage() == f"The keys of handle {refused.pk} could not be saved again."
    assert failure.exc_info is not None and str(failure.exc_info[1]) == "This row cannot be saved."

    assert Handle.objects.renormalize_handles() == KeyRepair(saved=1)
    assert _keyed(refused) == ("ada-2@bücher.example", Handle.NORMALIZATION_VERSION)


def test_a_capped_run_passes_a_row_whose_key_write_raises_and_still_saves_its_share(
    owner: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cap counts saved rows, so a row that cannot be saved neither uses it up nor holds the run on itself."""

    monkeypatch.setattr("angee.parties.managers.REPAIR_BATCH", 2)
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
        "Saved the keys of 1 handle(s) again.",
        "Stopped at the cap of 1 handle(s); 1 more await the next provision or renormalize_handles run.",
    ]
    # The cap line is for rows the cap left: a capped run that reached the end of the backlog has none.
    assert run() == ["Saved the keys of 1 handle(s) again."]


def test_without_a_cap_the_command_saves_every_batch_and_never_prints_the_cap_line(
    owner: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("angee.parties.managers.REPAIR_BATCH", 2)
    for index in range(5):
        _stored_with(owner, f"ada-{index}@xn--bcher-kva.example", "behind")
    output, errors = StringIO(), StringIO()

    call_command("renormalize_handles", stdout=output, stderr=errors)

    assert output.getvalue().splitlines() == ["Saved the keys of 5 handle(s) again."]
    assert errors.getvalue() == ""
    assert _behind() == 0


def test_an_operator_sees_how_many_rows_could_not_be_saved(owner: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Rows that failed are reported apart from rows a cap left: without a cap there is no cap line for them."""

    monkeypatch.setattr("angee.parties.managers.REPAIR_BATCH", 2)
    stale = [_stored_with(owner, f"ada-{index}@xn--bcher-kva.example", "behind") for index in range(3)]
    output, errors = StringIO(), StringIO()

    with _unsaveable(stale[1]):
        call_command("renormalize_handles", stdout=output, stderr=errors)

    assert output.getvalue().splitlines() == ["Saved the keys of 2 handle(s) again."]
    assert errors.getvalue().splitlines() == [
        "The keys of 1 handle(s) could not be saved; the log has each error, "
        "and the next provision or renormalize_handles run tries them again.",
    ]


def test_every_provision_saves_the_whole_backlog(owner: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """A deployment never depends on someone running the command, and a provision with nothing behind says so."""

    monkeypatch.setattr("angee.parties.managers.REPAIR_BATCH", 2)
    stale = [_stored_with(owner, f"ada-{index}@xn--bcher-kva.example", "behind") for index in range(5)]
    parties: Any = apps.get_app_config("parties")

    def provision() -> str:
        output = StringIO()
        parties.compose_provision(ProvisionContext("default", OutputWrapper(output), OutputWrapper(StringIO())))
        return output.getvalue()

    first = provision()

    assert "Saved the keys of 5 handle(s) again." in first
    assert "renormalize_handles run" not in first
    assert [_keyed(handle) for handle in stale] == [
        (f"ada-{index}@bücher.example", Handle.NORMALIZATION_VERSION) for index in range(5)
    ]
    assert "Saved the keys of 0 handle(s) again." in provision()


def test_the_keys_version_is_no_field_of_the_handle_a_reader_sees() -> None:
    """Bookkeeping of the key: the handle's GraphQL type names its fields one by one, and does not name this one."""

    # Imported here, after the concrete test models are registered: the source schema resolves them on import.
    schema = importlib.import_module("angee.parties.schema")
    shown = {field.python_name for field in schema.HandleType.__strawberry_definition__.fields}

    assert "normalized_value" in shown and "normalized_version" not in shown and "spelling_key" not in shown


_VERSION_1_VALUES: dict[tuple[str, str], str] = {
    ("email", "ada@example.test"): "ada@example.test",
    ("email", "  Ada.Example@Example.TEST "): "ada.example@example.test",
    ("email", "A.Lice+News@GMail.Com"): "alice@gmail.com",
    ("email", "Alice.Smith+work@googlemail.com"): "alicesmith@googlemail.com",
    ("email", "jörg@example.test"): "jörg@example.test",
    ("email", "Ada@Bücher.example"): "ada@bücher.example",
    ("email", "ada@XN--BCHER-KVA.example"): "ada@bücher.example",
    ("email", "ada@xn--9.example"): "ada@xn--9.example",
    ("email", "a.b@ gmail.com"): "a.b@gmail.com",
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
}
_DOMAINS: dict[str, str] = {
    "Example.TEST": "example.test",
    " Bücher.Example ": "bücher.example",
    "xn--bcher-kva.example": "bücher.example",
    "mail.XN--BCHER-KVA.example": "mail.bücher.example",
    "xn--strae-oqa.example": "xn--strae-oqa.example",
    "": "",
}

_IDNA_2003: dict[str, str] = {
    # Spellings IDNA 2003 sends as one domain, as Django writes a domain to send it.
    "bu\u0308cher.example": "bücher.example",  # NFD
    "\uff45xample.test": "example.test",  # a full-width letter
    "straße.example": "strasse.example",
}

# A ligature reads as many letters: U+FDFA as 18. A name of 84 labels of two (251 characters typed) would read as 3,107.
_LIGATURES = ".".join(["ﷺﷺ"] * 84)
_LONGER_THAN_A_DOMAIN: dict[str, str] = {
    "ﷺ.example": "صلى الله عليه وسلم.example",
    _LIGATURES: _LIGATURES,
}

# The local part's case: every character lower-cased but a non-ASCII one whose lower case would be ASCII or of another
# length, kept so that no non-ASCII character turns into another mailbox's ASCII letter; only ASCII whitespace trims.
_LOCAL_PARTS: dict[str, str] = {
    "JOSÉ@example.test": "josé@example.test",
    "MÜLLER@example.test": "müller@example.test",
    "\u212aate@example.test": "\u212aate@example.test",  # the Kelvin sign: kept, never kate@'s
    "\u0130stanbul@example.test": "\u0130stanbul@example.test",  # İ: kept, never i and a combining dot
    "\u212bngstr\u00f6m@example.test": "\u00e5ngstr\u00f6m@example.test",  # the Angstrom sign folds onto å
    "\u2126hm@example.test": "\u03c9hm@example.test",  # the Ohm sign folds onto ω
    "\u00a0kate@example.test": "\u00a0kate@example.test",  # a no-break space is no ASCII whitespace: kept
}

NORMALIZATION_SAMPLE: dict[int, tuple[dict[Any, str], ...]] = {
    1: (_VERSION_1_VALUES, _DOMAINS),
    2: (
        # The person-matching key: version 1's outputs, but for a domain with surrounding whitespace, which is now
        # read through the domain-spelling rule before the Gmail check, the spellings IDNA 2003 sends as one domain,
        # a name too long to be a domain, and the local part's case rule.
        {
            **_VERSION_1_VALUES,
            ("email", "a.b@ gmail.com"): "ab@gmail.com",
            **{("email", f"ada@{domain}"): f"ada@{one}" for domain, one in _IDNA_2003.items()},
            **{("email", f"ada@{domain}"): f"ada@{one}" for domain, one in _LONGER_THAN_A_DOMAIN.items()},
            **{("email", value): key for value, key in _LOCAL_PARTS.items()},
        },
        {**_DOMAINS, **_IDNA_2003, **_LONGER_THAN_A_DOMAIN},
        # The spelling key: letter case and domain spelling on email; none for what is no address, or off email.
        {
            ("email", "ada@example.test"): "ada@example.test",
            ("email", "  Ada.Example@Example.TEST "): "ada.example@example.test",
            ("email", "A.Lice+News@GMail.Com"): "a.lice+news@gmail.com",
            ("email", "jörg@example.test"): "jörg@example.test",
            ("email", "JÖRG@example.test"): "jörg@example.test",
            ("email", "Ada@Bücher.example"): "ada@bücher.example",
            ("email", "ada@XN--BCHER-KVA.example"): "ada@bücher.example",
            ("email", "ada@xn--strae-oqa.example"): "ada@xn--strae-oqa.example",
            ("email", "Straße@example.test"): "straße@example.test",
            ("email", "STRASSE@example.test"): "strasse@example.test",
            ("email", '"ADA EXAMPLE"@example.test'): '"ada example"@example.test',
            ("email", "a.b@ gmail.com"): "a.b@gmail.com",
            **{("email", f"Ada@{domain}"): f"ada@{one}" for domain, one in _IDNA_2003.items()},
            **{("email", f"Ada@{domain}"): f"ada@{one}" for domain, one in _LONGER_THAN_A_DOMAIN.items()},
            **{("email", value): key for value, key in _LOCAL_PARTS.items()},
            ("email", "n/a"): "",
            ("imessage", "Ada@Example.test"): "",
            ("phone", "+1 787 555 0100"): "",
            ("whatsapp", "+420 777 123 456"): "",
            **{(platform, "  Ada.Example "): "" for platform in (
                "discord", "matrix", "signal", "slack", "telegram", "youtube", "facebook", "instagram", "linkedin",
                "twitter", "other",
            )},
        },
    ),
}
"""What the normalization rule returns for a fixed sample, by ``Handle.NORMALIZATION_VERSION``.

The first mapping is ``Handle.normalize_value`` by ``(platform, value)``, with every
platform in it; the second is ``Handle.normalize_email_domain`` by domain; from version 2,
the third is ``Handle.spelling_key_of`` by ``(platform, value)``, with every platform in it.

A released version's outputs never change: a host stores keys under its number, and only a
new number makes provisioning write them again. A release is a tag (``v0.2.0`` the latest)
and the changelog's dated section; no release carries the version yet, so versions 1 and 2
are both unreleased. Version 1 is pinned as it was first stored; version 2, amended while
unreleased (a name too long to be a domain kept as typed, the local part's case rule), is
pinned as it will ship. Once a version is released, a change of the rule adds an entry under
a new number and leaves the released ones as the record of what they returned.
"""


def test_the_rule_returns_what_its_version_pins_and_a_released_versions_outputs_never_change() -> None:
    """Stored keys are written again only when the version moves, so a released number's outputs never change.

    The rule as it is must return what the sample pins under ``Handle.NORMALIZATION_VERSION``: a version still
    unreleased is amended in its entry, a released one is left as it shipped and the rule moves to a new number.
    """

    version = Handle.NORMALIZATION_VERSION
    assert version in NORMALIZATION_SAMPLE, (
        f"Handle.NORMALIZATION_VERSION is {version}: check in what the rule returns for the sample under that number."
    )
    values, domains, spellings = NORMALIZATION_SAMPLE[version]
    for mapping in (values, spellings):
        assert {platform for platform, _value in mapping} == set(Handle.Platform.values), (
            "The sample names every platform a handle may have: add a value for the platform it lacks."
        )
    returned = (
        {sample: Handle.normalize_value(*sample) for sample in values},
        {domain: Handle.normalize_email_domain(domain) for domain in domains},
        {sample: Handle.spelling_key_of(*sample) for sample in spellings},
    )
    assert returned == (values, domains, spellings), (
        f"The normalization rule no longer returns what version {version} pinned. A released version's outputs never "
        "change: if a release carries it, bump Handle.NORMALIZATION_VERSION and check in the new outputs under the new "
        "number, since provisioning writes stored keys again only when it moves; if none does, amend its entry."
    )


def test_the_local_part_keeps_exactly_the_characters_whose_lower_case_would_be_ascii_or_of_another_length() -> None:
    """The kept set is computed from ``str.lower``, never listed: today it is the Kelvin sign and ``İ``.

    A later Unicode version that widens or narrows the set fails here, where the change is seen.
    """

    kept = {
        character for character in map(chr, range(0x80, 0x110000))
        if character.lower() != character and Handle.normalize_local_part(character) == character
    }
    assert kept == {"\u212a", "\u0130"}
    assert Handle.normalize_local_part("\u212aATE") == "\u212aate"
    assert Handle.normalize_local_part("\u212bNGSTR\u00d6M") == "\u00e5ngstr\u00f6m"


_OLD_ASCII_RULE_DOMAINS = ("example.test", "Example.TEST", "xn--bcher-kva.example", "gmail.com", " Bücher.Example ")


@settings(deadline=None, max_examples=200)
@given(
    local=st.text(alphabet=st.characters(min_codepoint=0, max_codepoint=127, exclude_characters="@"), max_size=24),
    domain=st.sampled_from(_OLD_ASCII_RULE_DOMAINS),
    pad=st.sampled_from(("", " ", "\t", " \n ")),
)
def test_an_ascii_local_part_keys_as_the_rule_before_the_local_part_rule_keyed_it(
    local: str, domain: str, pad: str,
) -> None:
    """For a local part of ASCII characters the keys are what the whole-value ``lower()`` rule gave."""

    value = f"{pad}{local}@{domain}{pad}"
    before = value.strip().lower()
    before_local, _, before_domain = before.rpartition("@")
    old_spelling = f"{before_local}@{Handle.normalize_email_domain(before_domain)}"

    assert Handle.spelling_key_of("email", value) == old_spelling
    assert Handle.normalize_value("email", value) == Handle.normalize_value("email", before)


def test_an_address_in_another_case_of_a_non_ascii_letter_is_one_handle_and_a_sign_that_reads_ascii_is_its_own(
    owner: Any,
) -> None:
    """``JOSÉ@`` and ``josé@``, ``MÜLLER@`` and
    ``müller@`` land on one handle each; the Kelvin sign is no ``k``, so its address is a handle of
    its own, never ``kate@``'s; a leading no-break space is no ASCII whitespace, so that value is no address and keys as
    typed, never ``kate@``'s either."""

    def land(value: str) -> Any:
        with system_context(reason="tests.parties.handle_keys.local_part"):
            return Handle.objects.upsert(platform="email", value=value, created_by_id=owner.pk).pk

    assert land("JOSÉ@example.test") == land("josé@example.test")
    assert land("MÜLLER@example.test") == land("müller@example.test")
    kate = land("Kate@example.test")
    kelvin = land("\u212aate@example.test")
    assert kelvin != kate
    assert Handle._base_manager.get(pk=kelvin).spelling_key == "\u212aate@example.test"
    assert not Handle._base_manager.get(pk=kate).is_spelling_of("\u212aate@example.test")
    assert Handle._base_manager.get(pk=kate).is_spelling_of("KATE@example.test")
    assert Handle.is_email_address("\u00a0kate@example.test") is False
    assert Handle.spelling_key_of("email", "\u00a0kate@example.test") == "\u00a0kate@example.test"
    assert Handle.is_email_address(" kate@example.test ") is True


def _spelled(owner: Any, *values: str) -> list[Any]:
    """Live email handles as an earlier rule stored them, oldest first: each spelling a row of its own, unkeyed.

    ``bulk_create`` bypasses the handle's save, as the migration's state is reached: no spelling key, version 1.
    """

    with system_context(reason="tests.parties.handle_keys.spelled"):
        rows = Handle._base_manager.bulk_create(
            Handle(
                platform="email", value=value, normalized_value=Handle.normalize_value("email", value),
                normalized_version=1, spelling_key="", created_by=owner,
            )
            for value in values
        )
        return [Handle._base_manager.get(pk=row.pk) for row in rows]


def _live(value: str) -> list[Any]:
    """The live email handles any spelling of ``value`` names, by key."""

    with system_context(reason="tests.parties.handle_keys.live"):
        live = Handle._base_manager.filter(Handle.spellings_filter([value]), merged_into__isnull=True)
        return sorted(live.values_list("pk", flat=True))


def _stored(handle: Any) -> tuple[str, int, Any]:
    """A row's spelling key, its version and the handle it redirects to."""

    row = Handle._base_manager.get(pk=handle.pk)
    return row.spelling_key, row.normalized_version, row.merged_into_id


def test_a_live_email_handle_is_one_address_and_a_second_is_refused_before_anything_is_written(owner: Any) -> None:
    """``uq_handle_spelling_key`` holds the address; ``Handle.save`` refuses a second live spelling of it first."""

    with system_context(reason="tests.parties.handle_keys.constraint"):
        ada = Handle.objects.upsert(platform="email", value="Ada@Example.test", created_by_id=owner.pk)
        bob = Handle.objects.upsert(platform="email", value="bob@example.test", created_by_id=owner.pk)
        with CaptureQueriesContext(connection) as queries, pytest.raises(ValueOwned) as refused:
            Handle._base_manager.create(platform="email", value="ada@EXAMPLE.test", created_by=owner)
        # The database backstop, for a write the read cannot see: two keyed live rows of one key.
        with pytest.raises(IntegrityError), transaction.atomic():
            Handle._base_manager.filter(pk=bob.pk).update(spelling_key="ada@example.test")
        # A redirect keeps its survivor's key, and a row the key repair has not reached has none.
        ada.fold([bob])
        Handle._base_manager.filter(pk=bob.pk).update(spelling_key="ada@example.test")
        _spelled(owner, "Cy@Example.test", "cy@example.test")
        # Off email nothing beyond the exact value is unique: two spellings are two handles.
        Handle.objects.upsert(platform="telegram", value="Ada", created_by_id=owner.pk)
        Handle.objects.upsert(platform="telegram", value="ada", created_by_id=owner.pk)
        with pytest.raises(IntegrityError), transaction.atomic():
            Handle._base_manager.create(platform="telegram", value="ada", created_by=owner)

    assert refused.value.message_dict == {
        "value": [f"Handle {public_id_of(ada)} is this address; a second live handle of it is not kept."],
    }
    assert refused.value.error_dict["value"][0].code == "value_owned"
    statements = [query["sql"].lstrip().upper() for query in queries.captured_queries]
    assert not [sql for sql in statements if sql.startswith("INSERT")]
    assert Handle._base_manager.filter(platform="telegram").count() == 2


def test_two_live_handles_of_one_address_fold_into_the_older_and_what_refers_to_the_newer_moves_or_stays(
    owner: Any,
) -> None:
    """The repair keeps one live handle per address: correspondence stays where it was, a contact's claim moves."""

    older, newer = _spelled(owner, "Ada@Example.test", "ada@example.test")
    with system_context(reason="tests.parties.handle_keys.fold"):
        ada, again = (Person.objects.create(display_name=name, created_by=owner) for name in ("Ada", "Ada again"))
        Party.objects.assert_handle(ada, older, asserted_by=owner)
        Party.objects.assert_handle(again, newer, asserted_by=owner)
        channel = make_integration("handle-keys-fold-channel", model=Channel, backend_class="manual")
        message = Message._base_manager.create(
            channel=channel, thread=Thread.objects.create(owner=owner), external_id="handle-keys-fold",
            direction="inbound", status="synced", message_type="email", sender=newer, created_by=owner,
        )
        recipient = Recipient._base_manager.create(message=message, handle=newer, role="to")
        reaction = Reaction._base_manager.create(message=message, handle=newer, reaction="+1")

    assert Handle.objects.renormalize_handles() == KeyRepair(saved=2, folded=1)

    assert _live("ADA@example.test") == [older.pk]
    assert _stored(older) == ("ada@example.test", Handle.NORMALIZATION_VERSION, None)
    assert _stored(newer) == ("ada@example.test", Handle.NORMALIZATION_VERSION, older.pk)
    with system_context(reason="tests.parties.handle_keys.fold"):
        assert Message._base_manager.get(pk=message.pk).sender_id == newer.pk
        assert Recipient._base_manager.get(pk=recipient.pk).handle_id == newer.pk
        assert Reaction._base_manager.get(pk=reaction.pk).handle_id == older.pk
        assert sorted(Party.statements.filter(handle=older).values_list("subject_id", flat=True)) == sorted(
            [ada.pk, again.pk]
        )
        assert not Party.statements.filter(handle=newer).exists()
        # Every spelling lands on the survivor, which keeps the spelling first written.
        for spelling in ("ada@example.test", "ADA@example.test", "Ada@Example.test"):
            assert Handle.objects.upsert(platform="email", value=spelling).pk == older.pk
        assert Handle._base_manager.get(pk=older.pk).value == "Ada@Example.test"
        # The two contacts that held the two spellings now share the address: the same-person review has them.
        links = Party.objects.same_person_links(addresses=[("email", Handle.normalize_value("email", older.value))])
        assert {link.party for found in links.values() for link in found} == {ada.pk, again.pk}
    assert Handle.objects.renormalize_handles() == KeyRepair(saved=0)


def test_the_survivor_is_the_older_handle_by_creation_then_key(owner: Any) -> None:
    """``_age``: the row with the higher key survives when it was recorded first."""

    lower, higher = _spelled(owner, "bo@example.test", "Bo@Example.test")
    with system_context(reason="tests.parties.handle_keys.age"):
        Handle._base_manager.filter(pk=higher.pk).update(created_at=timezone.now() - timedelta(days=1))

    assert Handle.objects.renormalize_handles() == KeyRepair(saved=2, folded=1)

    assert (_stored(higher)[2], _stored(lower)[2]) == (None, higher.pk)


def test_three_spellings_of_one_address_end_as_one_live_handle(owner: Any) -> None:
    """Each deferred row asks again for the live owner of its key: an earlier fold of the run may have moved it."""

    rows = _spelled(owner, "Cy@Example.test", "CY@example.test", "cy@example.test")

    assert Handle.objects.renormalize_handles() == KeyRepair(saved=3, folded=2)

    assert _live("cy@example.test") == [rows[0].pk]
    assert [_stored(row)[2] for row in rows] == [None, rows[0].pk, rows[0].pk]


@pytest.mark.parametrize("when", ["before the fold's transaction", "between the owner's read and its lock"])
def test_a_deferred_handle_whose_owner_was_merged_away_meanwhile_is_keyed_alone(
    owner: Any, monkeypatch: pytest.MonkeyPatch, when: str,
) -> None:
    """A person merged the owner into another address meanwhile: the owner is asked again, nothing is left to fold."""

    owner_row, deferred = _spelled(owner, "Di@Example.test", "di@example.test")
    with system_context(reason="tests.parties.handle_keys.merged_meanwhile"):
        elsewhere = Handle.objects.upsert(platform="email", value="di@work.example", created_by_id=owner.pk)
    merged: list[Any] = []

    def merge_once() -> None:
        if not merged:
            merged.append(owner_row.pk)  # Before the fold, which takes the identity locks itself.
            elsewhere.fold([Handle._base_manager.get(pk=owner_row.pk)])

    if when == "before the fold's transaction":
        value_owner = Handle.value_owner
        asked: list[Any] = []

        def merged_after_the_batch(self: Any) -> Any:
            if self.pk == deferred.pk:
                asked.append(self.pk)
                if len(asked) == 2:
                    # The batch asked first and deferred the row; this is its fold's turn, before its transaction.
                    merge_once()
            return value_owner(self)

        monkeypatch.setattr(Handle, "value_owner", merged_after_the_batch)
    else:
        lock = Party.objects.lock_identity_rows

        def merged_before_the_lock(**kwargs: Any) -> Any:
            if owner_row.pk in kwargs["handle_ids"]:
                merge_once()
            return lock(**kwargs)

        monkeypatch.setattr(Party.objects, "lock_identity_rows", merged_before_the_lock)

    assert Handle.objects.renormalize_handles() == KeyRepair(saved=2)

    assert _stored(deferred) == ("di@example.test", Handle.NORMALIZATION_VERSION, None)
    assert _stored(owner_row) == ("di@example.test", Handle.NORMALIZATION_VERSION, elsewhere.pk)


def test_a_spelling_another_writer_committed_after_the_lookup_is_landed_on_not_refused(
    owner: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The insert's own read sees the other writer's row (``ValueOwned``) and ``upsert`` lands where it did."""

    with system_context(reason="tests.parties.handle_keys.committed_meanwhile"):
        committed = Handle.objects.upsert(platform="email", value="Hal@Example.test", created_by_id=owner.pk)
        find_identity = Handle.objects.find_identity
        asked: list[str] = []

        def missed_once(**identity: Any) -> Any:
            # The first lookup ran before the other writer's commit.
            asked.append(identity["value"])
            return None if len(asked) == 1 else find_identity(**identity)

        monkeypatch.setattr(Handle.objects, "find_identity", missed_once)
        landed = Handle.objects.upsert(platform="email", value="hal@example.test", created_by_id=owner.pk)

    assert (landed.pk, asked) == (committed.pk, ["hal@example.test", "hal@example.test"])
    assert _live("hal@example.test") == [committed.pk]


def refuse_handle_merges(record: Any) -> str | None:
    """A merge guard (``ANGEE_MERGE_GUARDS``) that keeps every handle, as an unfinished run keeps its subject."""

    return f"Work still runs on this record ({public_id_of(record)})." if isinstance(record, Handle) else None


@pytest.mark.parametrize("guard", ["a seeded row", "a registered guard"])
def test_a_fold_the_merge_refuses_is_reported_once_and_left_for_the_next_run(
    owner: Any, caplog: pytest.LogCaptureFixture, guard: str,
) -> None:
    """Nothing is written for the refused row: it stays live and unkeyed, its own spelling still finds it."""

    older, newer = _spelled(owner, "Ed@Example.test", "ed@example.test")
    guards = [f"{__name__}.refuse_handle_merges"] if guard == "a registered guard" else None
    if guards is None:
        with system_context(reason="tests.parties.handle_keys.seeded"):
            ledger = Resource.objects.create(
                source_addon="angee.workflows_parties", source_path="resources/demo/020_parties.handle.yaml",
                tier=Resource.Tier.DEMO, xref="duplicate_right_handle", content_hash="sha256:" + "0" * 64,
                target_model=Handle._meta.label, target_id=public_id_of(newer),
            )
    reason = (
        f"Work still runs on this record ({public_id_of(newer)})."
        if guards else
        f"This record ({public_id_of(newer)}) is loaded from angee.workflows_parties resources, which would recreate "
        "it; keep it instead."
    )
    output, errors = StringIO(), StringIO()

    with override_settings(**({"ANGEE_MERGE_GUARDS": guards} if guards else {})):
        with caplog.at_level(logging.WARNING, logger="angee.parties.managers"):
            call_command("renormalize_handles", stdout=output, stderr=errors)
        (warning,) = [record for record in caplog.records if record.name == "angee.parties.managers"]
        assert (warning.levelno, warning.exc_info) == (logging.WARNING, None)
        assert warning.getMessage() == (
            f"Handle {public_id_of(newer)} spells the address of handle {public_id_of(older)} and was not folded into "
            f"it: {reason}"
        )
        assert output.getvalue().splitlines() == ["Saved the keys of 1 handle(s) again."]
        assert errors.getvalue().splitlines() == [
            "1 handle(s) spell an address another handle owns and could not be folded; the log names each and why.",
        ]
        assert _stored(newer) == ("", 1, None)
        with system_context(reason="tests.parties.handle_keys.refused"):
            # Its own spelling still finds it, another spelling the address's keyed handle; no save keys it.
            assert Handle.objects.find_identity(platform="email", value="ed@example.test").pk == newer.pk
            assert Handle.objects.find_identity(platform="email", value="ED@example.test").pk == older.pk
            with pytest.raises(ValueOwned):
                Handle._base_manager.get(pk=newer.pk).save()
        # The next run tries again, and says so again.
        assert Handle.objects.renormalize_handles() == KeyRepair(saved=0, remaining=1, refused=1)

    if guards is None:
        with system_context(reason="tests.parties.handle_keys.seeded"):
            ledger.delete()
    assert Handle.objects.renormalize_handles() == KeyRepair(saved=1, folded=1)
    assert _stored(newer) == ("ed@example.test", Handle.NORMALIZATION_VERSION, older.pk)


def _revert(reason: str, handle: Any) -> None:
    """Revert, as a platform administrator, the changeset named ``reason`` that wrote ``handle``."""

    administrator = create_platform_admin(f"handle-keys-reverter-{handle.pk}")
    with system_context(reason="tests.parties.handle_keys.revert"):
        written = Handle.history.filter(id=handle.pk, changeset__change_reason=reason).latest("history_id").changeset
    with actor_context(administrator):
        written.with_actor(administrator).revert()


def test_a_revert_that_would_keep_a_second_live_handle_of_an_address_is_refused_and_writes_nothing(
    owner: Any, django_capture_on_commit_callbacks: Any,
) -> None:
    """The repair's fold, reverted, and an earlier merge whose address a newer spelling now holds, reverted."""

    older, newer = _spelled(owner, "Fay@Example.test", "fay@example.test")
    folding = "tests.parties.handle_keys.repair"
    with django_capture_on_commit_callbacks(execute=True), changeset(fresh=True, change_reason=folding):
        assert Handle.objects.renormalize_handles() == KeyRepair(saved=2, folded=1)
    with pytest.raises(ValueOwned) as refused:
        _revert(folding, newer)
    assert list(refused.value.message_dict) == ["value"]
    assert (_stored(older), _stored(newer)) == (
        ("fay@example.test", Handle.NORMALIZATION_VERSION, None),
        ("fay@example.test", Handle.NORMALIZATION_VERSION, older.pk),
    )

    # Before the upgrade a person merged one address into another; exact matching then made a case variant.
    merging = "tests.parties.handle_keys.merge"
    with system_context(reason="tests.parties.handle_keys.merge"):
        merged = Handle.objects.upsert(platform="email", value="gil@example.test", created_by_id=owner.pk)
        kept = Handle.objects.upsert(platform="email", value="gil@work.example", created_by_id=owner.pk)
    with (
        django_capture_on_commit_callbacks(execute=True),
        changeset(fresh=True, change_reason=merging),
        system_context(reason=merging),
    ):
        kept.fold([merged])
    (variant,) = _spelled(owner, "Gil@Example.test")
    assert Handle.objects.renormalize_handles() == KeyRepair(saved=1)
    with pytest.raises(ValueOwned):
        _revert(merging, merged)
    assert (_stored(merged)[2], _stored(variant)[2]) == (kept.pk, None)

    # A revert that restores display fields alone keys nothing and passes.
    naming = "tests.parties.handle_keys.name"
    with (
        django_capture_on_commit_callbacks(execute=True),
        changeset(fresh=True, change_reason=naming),
        system_context(reason=naming),
    ):
        Handle.objects.upsert(platform="email", value="Gil@Example.test", display_name="Gil")
    _revert(naming, variant)
    assert Handle._base_manager.get(pk=variant.pk).display_name == ""

    # A redirect the key repair has not reached, restored by a revert of its redirect alone: its address decides.
    restoring = "tests.parties.handle_keys.restore"
    (away,) = _spelled(owner, "ike@example.test")
    with system_context(reason=restoring):
        into = Handle.objects.upsert(platform="email", value="ike@work.example", created_by_id=owner.pk)
        # A version of the older rule's row in its history, keyed as that rule left it: not at all.
        away.label = "home"
        away.save(update_fields=["label", "updated_at"])
    with (
        django_capture_on_commit_callbacks(execute=True),
        changeset(fresh=True, change_reason=restoring),
        system_context(reason=restoring),
    ):
        into.fold([Handle._base_manager.get(pk=away.pk)])
    with system_context(reason=restoring):
        spelled = Handle.objects.upsert(platform="email", value="IKE@example.test", created_by_id=owner.pk)
    assert spelled.pk not in (away.pk, into.pk)
    with pytest.raises(ValueOwned):
        _revert(restoring, away)
    assert _stored(away) == ("", 1, into.pk)


def test_a_refused_save_spends_its_write_once_allowance_and_leaves_the_keys_as_they_were(owner: Any) -> None:
    """The refusal comes before the foundation's save, which would otherwise spend the allowance."""

    with system_context(reason="tests.parties.handle_keys.allowance"):
        Handle.objects.upsert(platform="email", value="Ivy@Example.test", created_by_id=owner.pk)
        other = Handle.objects.upsert(platform="email", value="ivy@work.example", created_by_id=owner.pk)
        row = Handle._base_manager.get(pk=other.pk)
        row.value = "ivy@example.test"
        row.allow_immutable_save("value")
        with pytest.raises(ValueOwned):
            row.save(update_fields=["value", "updated_at"])
        assert (row.spelling_key, row.normalized_value) == ("ivy@work.example", "ivy@work.example")
        row.value = "ivy@elsewhere.example"
        with pytest.raises(ValidationError) as refused:
            row.save(update_fields=["value", "updated_at"])

    assert refused.value.message_dict == {"value": ["This identity or receipt is immutable."]}
    assert Handle._base_manager.get(pk=other.pk).value == "ivy@work.example"


def test_the_repair_folds_only_spellings_of_an_email_address_never_a_shared_person_key(owner: Any) -> None:
    """Numbers sharing only their digits, aliases sharing a person key and values that are no address stay apart."""

    with system_context(reason="tests.parties.handle_keys.no_fold"):
        rows = Handle._base_manager.bulk_create(
            Handle(
                platform=platform, value=value, normalized_value=Handle.normalize_value(platform, value),
                normalized_version=1, spelling_key="", created_by=owner,
            )
            for platform, value in (
                ("phone", "1-800-FLOWERS"), ("phone", "1-800-COLLECT"),
                ("email", "a.b@gmail.com"), ("email", "ab@gmail.com"),
                ("email", "N/A"), ("email", "n/a"),
            )
        )

    assert Handle.objects.renormalize_handles() == KeyRepair(saved=6)

    assert [_stored(row) for row in rows] == [
        (key, Handle.NORMALIZATION_VERSION, None) for key in ("", "", "a.b@gmail.com", "ab@gmail.com", "", "")
    ]


def test_an_addresss_domain_is_its_spelling_keys_and_none_off_email() -> None:
    """``Handle.domain_of`` is what a caller compares an address's domain by."""

    assert Handle.domain_of("email", " Ada@XN--BCHER-KVA.Example ") == "bücher.example"
    assert Handle.domain_of("email", "no address") == ""
    assert Handle.domain_of("phone", "+1 555 0100") == ""


def test_the_email_handles_at_a_domain_are_found_by_any_spelling_of_it(owner: Any) -> None:
    """``HandleQuerySet.at_domain`` reads the stored key's domain through the rule; no domain names no handle."""

    with system_context(reason="tests.parties.handle_keys"):
        punycode, unicode, other = (
            Handle.objects.upsert(platform="email", value=value, created_by_id=owner.pk)
            for value in ("ada@xn--bcher-kva.example", "bob@bücher.example", "carl@other.example")
        )
        assert set(Handle.objects.at_domain("BÜCHER.example").values_list("pk", flat=True)) == {punycode.pk, unicode.pk}
        assert not Handle.objects.at_domain("").exists()
        assert set(Handle.objects.at_domain("other.example").values_list("pk", flat=True)) == {other.pk}
