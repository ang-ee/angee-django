"""Directory mappings: a consumer's own person columns filled from a pull-only source's fields.

The scripted pull-only source of ``test_parties_directory_backend`` serves each
contact's other fields only when asked; a donor composed into the test person
(``PersonFavourites``) owns the columns and their statement groups. Everything
runs through the real driver, the stream owner and the parties ingest.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import pytest
from django.core.checks import Error

from angee.integrate.states import LinkStatus
from angee.integrate.testing.models import RecordLink
from angee.messaging.testing.models import Person
from angee.parties.backends import MAPPINGS, DirectoryMapping
from angee.parties.checks import check_directory_mappings
from tests.stream_adapters import applied_history
from tests.test_parties_directory_backend import (
    _ADA,
    _GRACE,
    Pull,
    PullDirectoryBackend,
    TwoWayDirectoryBackend,
    _existing,
)
from tests.test_parties_directory_backend import pull as pull  # noqa: F401 -- the scripted pull-only directory

pytestmark = pytest.mark.django_db(transaction=True)


class ColourMapping(DirectoryMapping):
    """A contact's favourite colour, unless the source marks the contact as one not to record."""

    key = "colour"
    backend = PullDirectoryBackend.key
    fields = ("do_not_record", "fav_colour")
    aspects = ("colour",)

    def project(self, row: Mapping[str, Any]) -> dict[str, Any]:
        return {"favourite_colour": "" if row.get("do_not_record") else str(row.get("fav_colour") or "")}

    def apply_record(self, person: Any, values: Mapping[str, Any], *, link: Any) -> None:
        person.statements_for("colour").set(
            aspect="colour", values=values, asserted_by=link.asserting_source(), link=link
        )


class ColourNameMapping(ColourMapping):
    """The same group from another source field, so exchanging it for :class:`ColourMapping` restates it."""

    key = "colour_name"
    fields = ("colour_name",)

    def project(self, row: Mapping[str, Any]) -> dict[str, Any]:
        return {"favourite_colour": str(row.get("colour_name") or "")}


class NumberMapping(DirectoryMapping):
    """A contact's favourite number, under the same house rule as the colour."""

    key = "number"
    backend = PullDirectoryBackend.key
    fields = ("do_not_record", "fav_number")
    aspects = ("number",)

    def project(self, row: Mapping[str, Any]) -> dict[str, Any]:
        return {"favourite_number": None if row.get("do_not_record") else row.get("fav_number")}

    def apply_record(self, person: Any, values: Mapping[str, Any], *, link: Any) -> None:
        person.statements_for("number").set(
            aspect="number", values=values, asserted_by=link.asserting_source(), link=link
        )


class CountedNumberMapping(NumberMapping):
    """A favourite number the source sends as text; a value that is no number is one it cannot read."""

    key = "counted_number"
    fields = ("fav_number",)

    def project(self, row: Mapping[str, Any]) -> dict[str, Any]:
        value = row.get("fav_number")
        return {"favourite_number": int(value) if value not in (None, "") else None}


class RefusingNumberMapping(NumberMapping):
    """States the number, but cannot state one above a hundred (its value helper raises)."""

    key = "refusing_number"

    def apply_record(self, person: Any, values: Mapping[str, Any], *, link: Any) -> None:
        if (values["favourite_number"] or 0) > 100:
            raise ValueError(f"{values['favourite_number']} is out of range")
        super().apply_record(person, values, link=link)


class UndeclaredMapping(ColourMapping):
    """States a group it does not declare."""

    key = "undeclared"
    aspects = ()


class UnknownGroupMapping(ColourMapping):
    """Declares a group the person model does not have."""

    key = "unknown_group"
    aspects = ("shoe_size",)


class ElsewhereMapping(ColourMapping):
    """A mapping of another directory backend: the scripted one never asks for its field."""

    key = "elsewhere"
    backend = "elsewhere"
    fields = ("not_asked",)


class TwoWayMapping(ColourMapping):
    """A mapping of the scripted two-way backend."""

    key = "two_way_colour"
    backend = TwoWayDirectoryBackend.key


class CardDavMapping(ColourMapping):
    """A mapping of CardDAV, a backend that writes back."""

    key = "carddav_colour"
    backend = "carddav"


def _path(cls: type) -> str:
    return f"{cls.__module__}.{cls.__qualname__}"


def _register(settings: Any, *mappings: type[DirectoryMapping]) -> None:
    """Register ``mappings`` as consumer addons do, each under its key."""

    settings.ANGEE_DIRECTORY_MAPPING_CLASSES = {mapping.key: _path(mapping) for mapping in mappings}


def _colours(pull: Pull, **colours: str) -> None:
    """Give each named contact a favourite colour at the source."""

    for key, colour in colours.items():
        pull.source.fields[key] = {**pull.source.fields.get(key, {}), "fav_colour": colour}


def _person(pull: Pull, key: str) -> Any:
    return Person.objects.get(pk=pull.link(key).target_object_id)


def _stated(pull: Pull, key: str, aspect: str) -> Any:
    """The directory's current statement of ``aspect`` on the contact's person."""

    link = pull.link(key)
    rows = Person.statements.filter(subject_id=link.target_object_id, aspect=aspect, source_kind="directory")
    return rows.current().get()


def test_a_mapped_value_lands_on_the_person_through_the_link_and_a_source_change_reapplies_it(
    pull: Pull, settings: Any
) -> None:
    """Only the projection reaches the record: its payloads carry it under one key, and a change of it applies."""

    _colours(pull, ada="green", grace="blue")
    _register(settings, ColourMapping)
    pull.reopen()

    assert pull.sync().count == 2

    ada = pull.link("ada")
    assert _person(pull, "ada").favourite_colour == "green"
    statement = _stated(pull, "ada", "colour")
    assert (statement.status, statement.applied, statement.link_id) == ("asserted", True, ada.pk)
    assert ada.source_payload[MAPPINGS] == {"colour": {"favourite_colour": "green"}}
    assert ada.applied_base.mapped_payload == {**ada.source_payload["contact"], MAPPINGS: ada.source_payload[MAPPINGS]}
    assert "fav_colour" not in json.dumps([ada.source_payload, ada.applied_base.mapped_payload])
    # A re-read of the same source applies nothing; a changed mapped value is a changed record.
    pull.source.changed.update(pull.source.contacts)
    assert pull.sync().count == 0
    pull.source.put("ada", _ADA, fields={"fav_colour": "violet"})
    assert pull.sync().count == 1
    assert _person(pull, "ada").favourite_colour == "violet"
    assert _person(pull, "grace").favourite_colour == "blue"


def test_a_value_the_projection_drops_reaches_neither_the_person_nor_any_payload(pull: Pull, settings: Any) -> None:
    """The source sends the colour of a contact it marks not to record; nothing of it is kept anywhere."""

    pull.source.fields["ada"] = {"fav_colour": "vermilion", "fav_number": 7, "do_not_record": True}
    _register(settings, ColourMapping, NumberMapping)
    pull.reopen()

    assert pull.sync().count == 2

    person = _person(pull, "ada")
    assert (person.favourite_colour, person.favourite_number) == ("", None)
    link = pull.link("ada")
    assert link.source_payload[MAPPINGS] == {
        "colour": {"favourite_colour": ""},
        "number": {"favourite_number": None},
    }
    retained = list(RecordLink.history.filter(id=link.pk).values("source_payload", "mapped_payload"))
    stated = list(Person.statements.filter(subject_id=person.pk).values("favourite_colour", "favourite_number"))
    assert "vermilion" not in json.dumps([retained, stated])
    assert 7 not in [row["favourite_number"] for row in stated]


def test_the_backend_asks_its_source_for_exactly_the_fields_its_mappings_name(pull: Pull, settings: Any) -> None:
    """With no mapping it asks for nothing more; with mappings, for the sorted union of their fields and no other."""

    pull.sync()
    assert pull.backend.mapped_fields() == ()
    assert set(pull.source.requested) == {()}

    _register(settings, ColourMapping, NumberMapping, ElsewhereMapping)
    pull.source.requested.clear()
    pull.reopen()
    pull.sync()

    assert pull.backend.mapped_fields() == ("do_not_record", "fav_colour", "fav_number")
    assert set(pull.source.requested) == {("do_not_record", "fav_colour", "fav_number")}
    assert set(pull.link("ada").source_payload[MAPPINGS]) == {"colour", "number"}


def test_adding_a_mapping_replays_each_record_once_and_an_unchanged_set_replays_nothing(
    pull: Pull, settings: Any
) -> None:
    """Each registered mapping is one part of the contacts stream's mapping; a new part opens one baseline."""

    assert pull.sync().count == 2
    ada = pull.link("ada")
    assert MAPPINGS not in ada.source_payload
    assert ada.applied_base.mapped_payload == ada.source_payload["contact"]
    plain = pull.stream
    assert (pull.reopen().pk, pull.sync().count) == (plain.pk, 0)

    _colours(pull, ada="green")
    _register(settings, ColourMapping)
    composed = pull.reopen()
    assert (composed.generation, composed.mapping_version) == (plain.generation + 1, 2)
    assert composed.retained_contributions == (("colour", 1),)
    applications = applied_history().count()
    assert pull.sync().count == 2
    assert applied_history().count() == applications + 2
    assert {link.applied_base.mapping_version for link in RecordLink.objects.filter(stream=pull.stream)} == {2}

    # The same set again opens nothing and re-reading every record applies nothing.
    assert pull.reopen().pk == composed.pk
    pull.source.changed.update(pull.source.contacts)
    assert pull.sync().count == 0
    assert _person(pull, "ada").favourite_colour == "green"


def test_exchanging_one_mapping_for_another_replays_and_restates_the_group(pull: Pull, settings: Any) -> None:
    pull.source.fields["ada"] = {"fav_colour": "green", "colour_name": "teal"}
    _register(settings, ColourMapping)
    pull.reopen()
    pull.sync()
    assert _person(pull, "ada").favourite_colour == "green"
    before = pull.stream

    _register(settings, ColourNameMapping)
    exchanged = pull.reopen()

    assert exchanged.generation == before.generation + 1
    assert exchanged.mapping_version > before.mapping_version
    assert pull.sync().count == 2
    assert pull.link("ada").source_payload[MAPPINGS] == {"colour_name": {"favourite_colour": "teal"}}
    assert _person(pull, "ada").favourite_colour == "teal"
    assert _stated(pull, "ada", "colour").favourite_colour == "teal"


def test_withdrawing_the_link_withdraws_what_the_mapping_stated(pull: Pull, settings: Any) -> None:
    _colours(pull, grace="blue")
    _register(settings, ColourMapping)
    pull.reopen()
    pull.sync()
    statement = _stated(pull, "grace", "colour")
    assert statement.status == "asserted"

    del pull.source.contacts["grace"]
    pull.source.deleted.add("grace")
    assert pull.sync().count == 1

    assert pull.link("grace").status == LinkStatus.TOMBSTONE
    statement.refresh_from_db()
    assert statement.status == "withdrawn"


def test_a_persons_own_value_wins_by_the_donors_precedence(pull: Pull, settings: Any) -> None:
    """The donor ranks a person above a directory, so the source's later change waits behind the person's own."""

    _colours(pull, ada="green")
    _register(settings, ColourMapping)
    pull.reopen()
    pull.sync()
    _person(pull, "ada").with_actor(pull.directory.owner).statements_for("colour").set(
        aspect="colour", values={"favourite_colour": "blue"}
    )

    pull.source.put("ada", _ADA, fields={"fav_colour": "violet"})
    assert pull.sync().count == 1

    assert _person(pull, "ada").favourite_colour == "blue"
    statement = _stated(pull, "ada", "colour")
    assert (statement.favourite_colour, statement.applied) == ("violet", False)


def test_an_adopted_person_is_mapped_like_a_minted_one(pull: Pull, settings: Any) -> None:
    person, _handle = _existing(pull.directory.owner)
    _colours(pull, ada="green")
    _register(settings, ColourMapping)
    pull.reopen()

    pull.sync()

    ada = pull.link("ada")
    assert ada.target_object_id == str(person.pk)
    person.refresh_from_db()
    assert person.favourite_colour == "green"
    assert _stated(pull, "ada", "colour").link_id == ada.pk


def test_a_mapping_applies_to_the_backend_it_names_only(pull: Pull, settings: Any) -> None:
    """Another backend's contacts stream composes no part and its payloads keep their shape."""

    _register(settings, ColourMapping)
    other = TwoWayDirectoryBackend(pull.directory)
    other.source = pull.source

    assert [part.key for part in pull.backend.contacts_stream(pull.stream.container).mapping_contributions] == [
        "colour"
    ]
    assert other.contacts_stream(pull.stream.container).mapping_contributions == ()
    assert other.mapped_fields() == ()
    assert MAPPINGS not in other.observe(_GRACE, row={"fav_colour": "blue"})


def test_removing_the_last_mapping_replays_each_record_once_without_its_projection(pull: Pull, settings: Any) -> None:
    _colours(pull, ada="green")
    _register(settings, ColourMapping)
    pull.reopen()
    pull.sync()
    composed = pull.stream

    _register(settings)
    removed = pull.reopen()

    assert (removed.generation, removed.retained_contributions) == (composed.generation + 1, (("", 1),))
    assert removed.mapping_version > composed.mapping_version
    assert pull.sync().count == 2
    ada = pull.link("ada")
    assert MAPPINGS not in ada.source_payload
    assert ada.applied_base.mapped_payload == ada.source_payload["contact"]


def test_removing_a_mapping_withdraws_what_it_stated_on_its_replay(pull: Pull, settings: Any) -> None:
    _colours(pull, ada="green")
    _register(settings, ColourMapping)
    pull.reopen()
    pull.sync()
    statement = _stated(pull, "ada", "colour")

    _register(settings)
    pull.reopen()
    pull.sync()

    statement.refresh_from_db()
    assert statement.status == "withdrawn"


def test_the_replay_withdraws_only_the_groups_no_registered_mapping_and_no_ingest_states(
    pull: Pull, settings: Any
) -> None:
    """The removed mapping's group goes; a mapping still registered and the contact's own groups stay."""

    pull.source.fields["ada"] = {"fav_colour": "green", "fav_number": 7}
    _register(settings, ColourMapping, NumberMapping)
    pull.reopen()
    pull.sync()

    _register(settings, NumberMapping)
    pull.reopen()
    pull.sync()

    link = pull.link("ada")
    statuses = dict(
        Person.statements.filter(subject_id=link.target_object_id, link=link).current().values_list("aspect", "status")
    )
    assert statuses == {"colour": "withdrawn", "number": "asserted", "name": "asserted", "dates": "asserted"}
    assert _person(pull, "ada").favourite_number == 7


def _refusals(pull: Pull, key: str) -> list[Any]:
    return list(pull.link(key).failures(open_only=True))


def test_a_value_a_projection_cannot_read_refuses_its_record_alone_until_the_source_changes_it(
    pull: Pull, settings: Any
) -> None:
    """The refusal names the mapping and never the value; the rest of the page lands, and a fixed value applies."""

    pull.source.fields.update(ada={"fav_number": "seven"}, grace={"fav_number": "7"})
    _register(settings, CountedNumberMapping)
    pull.reopen()

    result = pull.sync()

    assert len(result.failure_ids) == 1
    assert _person(pull, "grace").favourite_number == 7
    assert pull.link("ada").target_object_id is None
    (refusal,) = _refusals(pull, "ada")
    assert "counted_number" in json.dumps(refusal.model_dump(mode="json"), default=str)
    assert "seven" not in json.dumps([refusal.model_dump(mode="json"), pull.link("ada").source_payload], default=str)

    pull.source.put("ada", _ADA, fields={"fav_number": "8"})
    assert pull.sync().count == 1
    assert _person(pull, "ada").favourite_number == 8
    assert not _refusals(pull, "ada")


def test_a_value_a_mapping_cannot_state_refuses_its_record_alone_and_lands_nothing_of_it(
    pull: Pull, settings: Any
) -> None:
    """The record's savepoint rolls back its ingest too; the rest of the page lands."""

    pull.source.fields.update(ada={"fav_number": 1000}, grace={"fav_number": 7})
    _register(settings, RefusingNumberMapping)
    pull.reopen()

    result = pull.sync()

    assert len(result.failure_ids) == 1
    assert _person(pull, "grace").favourite_number == 7
    assert pull.link("ada").target_object_id is None
    (refusal,) = _refusals(pull, "ada")
    assert "refusing_number" in json.dumps(refusal.model_dump(mode="json"), default=str)
    assert "1000" not in json.dumps(refusal.model_dump(mode="json"), default=str)

    pull.source.put("ada", _ADA, fields={"fav_number": 8})
    assert pull.sync().count == 1
    assert _person(pull, "ada").favourite_number == 8


def test_a_mapping_of_a_backend_that_writes_back_or_of_no_backend_fails_the_system_check(settings: Any) -> None:
    """Mappings are for pull-only directories; each one names a registered backend and claims its own key."""

    settings.ANGEE_DIRECTORY_BACKEND_CLASSES = {
        **settings.ANGEE_DIRECTORY_BACKEND_CLASSES,
        PullDirectoryBackend.key: _path(PullDirectoryBackend),
        TwoWayDirectoryBackend.key: _path(TwoWayDirectoryBackend),
    }
    _register(settings, ColourMapping, NumberMapping)
    assert check_directory_mappings() == []

    def ids(*mappings: type[DirectoryMapping]) -> list[str]:
        _register(settings, *mappings)
        return [error.id for error in check_directory_mappings() if isinstance(error, Error)]

    assert ids(ColourMapping, CardDavMapping) == ["parties.E002"]
    assert ids(TwoWayMapping) == ["parties.E002"]
    assert ids(ElsewhereMapping) == ["parties.E001"]
    # A mapping declares the person groups it states, so its removal can withdraw them.
    assert ids(UndeclaredMapping) == ["parties.E003"]
    assert ids(UnknownGroupMapping) == ["parties.E003"]
    # A mapping claims the one key it is registered under, so one mapping registered twice fails.
    settings.ANGEE_DIRECTORY_MAPPING_CLASSES = {"colour": _path(ColourMapping), "colour_again": _path(ColourMapping)}
    assert [error.id for error in check_directory_mappings()] == ["angee.E004"]
