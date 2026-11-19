"""Directory mappings: a consumer's own person columns filled from a pull-only source's fields.

The scripted pull-only source of ``test_parties_directory_backend`` serves each
contact's other fields only when asked; a donor composed into the test person
(``PersonFavourites``) owns the columns and their statement groups. Everything
runs through the real driver, the stream owner and the parties ingest.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

import pytest
from django.apps import apps
from django.core.checks import Error
from django.core.exceptions import ImproperlyConfigured

from angee.integrate.states import LinkStatus
from angee.integrate.streams import begin_stream_cycle
from angee.integrate.testing.models import RecordLink
from angee.messaging.testing.models import Handle, Party, Person
from angee.parties.backends import MAPPINGS, DirectoryMapping, ParsedContact
from angee.parties.checks import check_directory_mappings
from angee.resources.testing.models import Resource
from angee.tags.testing.models import Tag, TagAssignment
from tests.conftest import MarkdownPage, RecordBinding, Vault
from tests.stream_adapters import applied_history
from tests.test_parties_directory_backend import (
    _ADA,
    _GRACE,
    Pull,
    PullDirectoryBackend,
    TwoWayDirectoryBackend,
    _existing,
)
from tests.test_parties_directory_backend import adoption as adoption  # noqa: F401 -- the directory adopting
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


class BrokenProjectionMapping(NumberMapping):
    """A projection with a bug: it measures a number, a programming error rather than a value it cannot read."""

    key = "broken_projection"

    def project(self, row: Mapping[str, Any]) -> dict[str, Any]:
        return {"favourite_number": len(row.get("fav_number"))}  # type: ignore[arg-type]


class BrokenApplyMapping(NumberMapping):
    """An ``apply_record`` with a bug: it calls its base without the link."""

    key = "broken_apply"

    def apply_record(self, person: Any, values: Mapping[str, Any], *, link: Any) -> None:
        super().apply_record(person, values)  # type: ignore[call-arg]


class MislabelledMapping(ColourMapping):
    """States the colour group while declaring the number group."""

    key = "mislabelled"
    aspects = ("number",)


class NameMapping(ColourMapping):
    """Declares a group the contact ingest itself states."""

    key = "name_mapping"
    aspects = ("name",)


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


class HouseNameMapping(DirectoryMapping):
    """The name a house uses for a person: it refines the contact's nickname, so the ingest states it."""

    key = "house_name"
    backend = PullDirectoryBackend.key
    fields = ("house_name",)
    refines = ("nickname",)

    def project(self, row: Mapping[str, Any]) -> dict[str, Any]:
        return {"nickname": str(row.get("house_name") or "")}

    def refine(self, contact: ParsedContact, values: Mapping[str, Any]) -> ParsedContact:
        return replace(contact, nickname=values["nickname"]) if values["nickname"] else contact


class AnotherHouseNameMapping(HouseNameMapping):
    """A second mapping of the same backend refining the same field."""

    key = "house_name_again"


class ElsewhereRefiningMapping(HouseNameMapping):
    """Refines the same field for another backend's contacts."""

    key = "elsewhere_house_name"
    backend = "elsewhere"


class PhotoRefiningMapping(HouseNameMapping):
    """Would refine the contact's photo, which is no mapping's to refine."""

    key = "photo_refining"
    refines = ("photo",)


class UndeclaredRefinementMapping(HouseNameMapping):
    """Refines the nickname while declaring the given name."""

    key = "undeclared_refinement"
    refines = ("given_name",)


class FlagsMapping(DirectoryMapping):
    """Plain tags from a source's multi-select field, and the preferred mark on the channel the contact chose.

    Neither is a statement group, so it declares none. The mark on the contact's own email or phone is a
    refinement of the contact, stated by the ingest; on a handle the contact does not bring (a WhatsApp
    handle) and the tags, it writes through the owners' own verbs.
    """

    key = "flags"
    backend = PullDirectoryBackend.key
    fields = ("flags", "channel")
    refines = ("emails", "phones")

    def project(self, row: Mapping[str, Any]) -> dict[str, Any]:
        flags = sorted({flag.strip() for flag in str(row.get("flags") or "").split(";") if flag.strip()})
        channels = {"email": "emails", "text": "phones", "whatsapp": Handle.Platform.WHATSAPP}
        return {"tags": flags, "preferred": str(channels.get(str(row.get("channel"))) or "")}

    def refine(self, contact: ParsedContact, values: Mapping[str, Any]) -> ParsedContact:
        points = values["preferred"]
        if points not in ("emails", "phones") or not getattr(contact, points):
            return contact
        (value, label, _preferred), *rest = getattr(contact, points)
        return replace(contact, **{points: ((value, label, True), *rest)})

    def apply_record(self, person: Any, values: Mapping[str, Any], *, link: Any) -> None:
        tags = [str(tag.sqid) for tag in Tag.objects.filter(name__in=values["tags"])]
        TagAssignment.objects.attach("parties/party", str(person.sqid), tags)
        if values["preferred"] == Handle.Platform.WHATSAPP:
            for handle in Handle.objects.filter(party=person.pk, platform=Handle.Platform.WHATSAPP).order_by("pk"):
                Handle.objects.upsert(platform=handle.platform, value=handle.value, is_preferred=True)


class AddressAddingMapping(FlagsMapping):
    """Would add an address to the contact while marking one preferred: a refinement sets only the flag."""

    key = "address_adding"

    def refine(self, contact: ParsedContact, values: Mapping[str, Any]) -> ParsedContact:
        return replace(contact, phones=(*contact.phones, ("+14155550199", "", True)))


class RememberMapping(DirectoryMapping):
    """A source's free text about a person, written as a note in their folder of the people vault."""

    key = "remember"
    backend = PullDirectoryBackend.key
    fields = ("remember",)

    def project(self, row: Mapping[str, Any]) -> dict[str, Any]:
        return {"remember": str(row.get("remember") or "")}

    def apply_record(self, person: Any, values: Mapping[str, Any], *, link: Any) -> None:
        RecordBinding.objects.write_sourced_page(
            person,
            vault=Vault.objects.for_resource(Vault.PEOPLE),
            role="remember",
            folder_title=f"{person.display_name} ({person.sqid})",
            title=f"{person.display_name}: remember ({person.sqid})",
            body=values["remember"],
            written=self.applied(link).get("remember"),
        )


class ProfileMapping(DirectoryMapping):
    """Parties' own languages and occupation groups, from a source that says "both" for two languages."""

    key = "profile"
    backend = PullDirectoryBackend.key
    fields = ("spoken", "job")
    aspects = ("languages", "occupation")

    def project(self, row: Mapping[str, Any]) -> dict[str, Any]:
        spoken = {"english": ["en"], "spanish": ["es"], "both": ["en", "es"]}
        return {"languages": spoken.get(str(row.get("spoken") or ""), []), "occupation": str(row.get("job") or "")}

    def apply_record(self, person: Any, values: Mapping[str, Any], *, link: Any) -> None:
        for aspect in self.aspects:
            person.statements_for(aspect).set(
                aspect=aspect, values={aspect: values[aspect]}, asserted_by=link.asserting_source(), link=link
            )


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


def test_a_mapping_states_parties_own_languages_and_occupation_and_a_persons_own_value_wins(
    pull: Pull, settings: Any
) -> None:
    """No neutral contact carries them, so a mapping states them; a person's statement outranks the directory's."""

    pull.source.fields["ada"] = {"spoken": "both", "job": "Cartographer"}
    settings.ANGEE_DIRECTORY_BACKEND_CLASSES = {
        **settings.ANGEE_DIRECTORY_BACKEND_CLASSES,
        PullDirectoryBackend.key: _path(PullDirectoryBackend),
    }
    _register(settings, ProfileMapping)
    assert check_directory_mappings() == []
    pull.reopen()

    pull.sync()

    person = _person(pull, "ada")
    assert (person.languages, person.occupation) == (["en", "es"], "Cartographer")
    assert _stated(pull, "ada", "languages").link_id == pull.link("ada").pk
    person.with_actor(pull.directory.owner).statements_for("languages").set(
        aspect="languages", values={"languages": ["es"]}
    )

    pull.source.put("ada", _ADA, fields={"spoken": "english", "job": "Surveyor"})
    assert pull.sync().count == 1

    person = _person(pull, "ada")
    assert (person.languages, person.occupation) == (["es"], "Surveyor")
    assert (_stated(pull, "ada", "languages").languages, _stated(pull, "ada", "languages").applied) == (["en"], False)


def _checked(settings: Any, *mappings: type[DirectoryMapping]) -> list[str]:
    """Register ``mappings`` with the scripted backend registered too; return the system check's error ids."""

    settings.ANGEE_DIRECTORY_BACKEND_CLASSES = {
        **settings.ANGEE_DIRECTORY_BACKEND_CLASSES,
        PullDirectoryBackend.key: _path(PullDirectoryBackend),
    }
    _register(settings, *mappings)
    return [error.id for error in check_directory_mappings() if isinstance(error, Error)]


def _name_statement(pull: Pull, key: str) -> Any:
    return _stated(pull, key, "name")


def test_a_refined_nickname_lands_through_the_ingests_own_statement_and_a_source_change_reapplies_it(
    pull: Pull, settings: Any
) -> None:
    """The refined contact is what the record compares and the ingest states; the mapping states no group."""

    pull.source.fields["ada"] = {"house_name": "Addie"}
    assert _checked(settings, HouseNameMapping) == []
    pull.reopen()

    assert pull.sync().count == 2

    ada = pull.link("ada")
    assert _person(pull, "ada").nickname == "Addie"
    assert ada.source_payload["contact"]["nickname"] == "Addie"
    assert ada.applied_base.mapped_payload["nickname"] == "Addie"
    assert (_name_statement(pull, "ada").nickname, _name_statement(pull, "ada").link_id) == ("Addie", ada.pk)
    assert _person(pull, "grace").nickname == ""
    # A re-read of the same source applies nothing; a changed source value is a changed record.
    pull.source.changed.update(pull.source.contacts)
    assert pull.sync().count == 0
    pull.source.put("ada", _ADA, fields={"house_name": "Countess"})
    assert pull.sync().count == 1
    assert _person(pull, "ada").nickname == "Countess"


def test_removing_a_refining_mapping_replays_and_the_ingest_restates_the_sources_own_value(
    pull: Pull, settings: Any
) -> None:
    pull.source.fields["ada"] = {"house_name": "Addie"}
    _register(settings, HouseNameMapping)
    pull.reopen()
    pull.sync()

    _register(settings)
    pull.reopen()
    assert pull.sync().count == 2

    assert _person(pull, "ada").nickname == ""
    assert _name_statement(pull, "ada").nickname == ""


def test_a_persons_own_nickname_still_wins_over_a_refined_one_by_rank(pull: Pull, settings: Any) -> None:
    pull.source.fields["ada"] = {"house_name": "Addie"}
    _register(settings, HouseNameMapping)
    pull.reopen()
    pull.sync()
    person = _person(pull, "ada")
    names = {name: getattr(person, name) for name in Person.statements.model.statement_aspects["name"].fields}
    person.with_actor(pull.directory.owner).statements_for("name").set(
        aspect="name", values={**names, "nickname": "Lady Ada"}
    )

    pull.source.put("ada", _ADA, fields={"house_name": "Countess"})
    assert pull.sync().count == 1

    assert _person(pull, "ada").nickname == "Lady Ada"
    assert (_name_statement(pull, "ada").nickname, _name_statement(pull, "ada").applied) == ("Countess", False)


def test_refining_a_field_the_mapping_does_not_declare_fails_the_sync(pull: Pull, settings: Any) -> None:
    pull.source.fields["ada"] = {"house_name": "Addie"}
    _register(settings, UndeclaredRefinementMapping)
    pull.reopen()

    with pytest.raises(ImproperlyConfigured, match="nickname"):
        pull.sync()


def _tags(person: Any) -> list[str]:
    return sorted(TagAssignment.objects.for_record(person).values_list("tag__name", flat=True))


def test_a_mapping_declaring_no_group_tags_and_marks_preferred_and_a_rerun_changes_nothing(
    pull: Pull, settings: Any
) -> None:
    """Tags and the preferred mark go through their owners' verbs; neither is taken back when the mapping goes."""

    for name in ("Press", "Regular", "Unused"):
        Tag.objects.create(name=name)
    pull.source.fields["ada"] = {"flags": "Press;Regular", "channel": "whatsapp"}
    pull.sync()
    person = _person(pull, "ada")
    whatsapp = Handle.objects.upsert(platform=Handle.Platform.WHATSAPP, value="+14155550100")
    Party.objects.assert_handle(person, whatsapp, asserted_by=pull.directory.owner)
    assert _checked(settings, FlagsMapping) == []
    pull.reopen()

    assert pull.sync().count == 2

    assert _tags(person) == ["Press", "Regular"]
    whatsapp.refresh_from_db()
    assert whatsapp.is_preferred is True
    assert _tags(_person(pull, "grace")) == []
    # A re-read applies nothing and writes nothing.
    edges, marks = TagAssignment.history.count(), Handle.history.count()
    pull.source.changed.update(pull.source.contacts)
    assert pull.sync().count == 0
    assert (TagAssignment.history.count(), Handle.history.count()) == (edges, marks)
    # A tag the source drops stays: an import adds tags, it never takes them back.
    pull.source.put("ada", _ADA, fields={"flags": "Press", "channel": "whatsapp"})
    assert pull.sync().count == 1
    assert _tags(person) == ["Press", "Regular"]
    assert Handle.history.count() == marks

    _register(settings)
    pull.reopen()
    pull.sync()

    assert _tags(person) == ["Press", "Regular"]
    whatsapp.refresh_from_db()
    assert whatsapp.is_preferred is True


def test_a_preferred_mark_on_the_contacts_own_phone_is_stated_by_the_ingest_and_goes_with_the_mapping(
    pull: Pull, settings: Any
) -> None:
    """The refined flag lands with the phone, a changed record writes the handle no more, and removal restores it."""

    pull.source.put("ada", replace(_ADA, phones=(("+14155550123", "cell", False),)), fields={"channel": "text"})
    assert _checked(settings, FlagsMapping) == []
    pull.reopen()
    pull.sync()
    ada = pull.link("ada")
    phone = Handle.objects.get(platform=Handle.Platform.PHONE, party=ada.target_object_id)
    assert phone.is_preferred is True
    assert ada.source_payload["contact"]["phones"] == [["+14155550123", "cell", True]]

    marks = Handle.history.filter(id=phone.pk).count()
    pull.source.put("ada", replace(_ADA, phones=(("+14155550123", "cell", False),), notes="Moved"), fields={
        "channel": "text"
    })
    assert pull.sync().count == 1
    phone.refresh_from_db()
    assert phone.is_preferred is True
    assert Handle.history.filter(id=phone.pk).count() == marks

    _register(settings)
    pull.reopen()
    pull.sync()
    phone.refresh_from_db()
    assert phone.is_preferred is False


def test_a_refinement_of_the_contacts_addresses_sets_only_a_preferred_flag(pull: Pull, settings: Any) -> None:
    """Adding, removing or relabelling an address is the source's alone: such a refinement fails the extract."""

    pull.source.put("ada", replace(_ADA, phones=(("+14155550123", "cell", False),)), fields={"channel": "text"})
    _register(settings, AddressAddingMapping)
    pull.reopen()

    with pytest.raises(ImproperlyConfigured, match="preferred"):
        pull.sync()


def test_a_mapping_writes_a_sourced_note_that_follows_the_source_until_a_person_edits_it(
    pull: Pull, settings: Any
) -> None:
    """The link remembers what the mapping last wrote; an edited note keeps its text and the newer one below."""

    Resource.objects.load_addons((apps.get_app_config("knowledge"),), tiers=[Resource.Tier.INSTALL])
    pull.source.fields["ada"] = {"remember": "Prefers tea."}
    assert _checked(settings, RememberMapping) == []
    pull.reopen()
    pull.sync()
    person = _person(pull, "ada")

    def note() -> Any:
        (binding,) = RecordBinding.objects.for_record(person, role="remember")
        return MarkdownPage.objects.get(pk=binding.page_id)

    assert note().body == "Prefers tea."
    revisions = MarkdownPage.history.filter(id=note().pk).count()
    pull.source.changed.update(pull.source.contacts)
    assert pull.sync().count == 0
    pull.source.put("ada", replace(_ADA, notes="Moved"), fields={"remember": "Prefers tea."})
    assert pull.sync().count == 1
    assert MarkdownPage.history.filter(id=note().pk).count() == revisions

    pull.source.put("ada", _ADA, fields={"remember": "Prefers green tea."})
    pull.sync()
    assert note().body == "Prefers green tea."

    MarkdownPage.objects.write_body(note(), "Prefers green tea. Ask about the garden.")
    pull.source.put("ada", _ADA, fields={"remember": "Prefers coffee."})
    assert pull.sync().count == 1
    assert note().body.startswith("Prefers green tea. Ask about the garden.")
    assert "> Prefers coffee." in note().body
    history = RecordLink.history.filter(id=pull.link("ada").pk).values_list("source_payload", flat=True)
    assert any(payload[MAPPINGS]["remember"]["remember"] == "Prefers coffee." for payload in history if payload)

    # The note stays when the mapping goes.
    _register(settings)
    pull.reopen()
    pull.sync()
    assert note().body.startswith("Prefers green tea. Ask about the garden.")


@pytest.mark.usefixtures("adoption")
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
    pull: Pull, settings: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """The refusal names the mapping and never the value; the rest of the page lands, and a fixed value applies.

    The refusal is logged with the mapping's key and the record's key, never the value.
    """

    pull.source.fields.update(ada={"fav_number": "seven"}, grace={"fav_number": "7"})
    _register(settings, CountedNumberMapping)
    pull.reopen()

    with caplog.at_level(logging.WARNING, logger="angee.parties.backends"):
        result = pull.sync()

    assert len(result.failure_ids) == 1
    assert _person(pull, "grace").favourite_number == 7
    assert pull.link("ada").target_object_id is None
    (refusal,) = _refusals(pull, "ada")
    assert "counted_number" in json.dumps(refusal.model_dump(mode="json"), default=str)
    assert "seven" not in json.dumps([refusal.model_dump(mode="json"), pull.link("ada").source_payload], default=str)
    (logged,) = [record for record in caplog.records if record.name == "angee.parties.backends"]
    assert "counted_number" in logged.getMessage() and "ada" in logged.getMessage()
    assert "seven" not in logged.getMessage() and logged.exc_info is None

    pull.source.put("ada", _ADA, fields={"fav_number": "8"})
    assert pull.sync().count == 1
    assert _person(pull, "ada").favourite_number == 8
    assert not _refusals(pull, "ada")


def test_a_refused_record_applies_on_its_rescan_once_its_mapping_reads_the_value(
    pull: Pull, settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A code fix needs no source change: the rescan reads the record again and the fixed mapping applies it.

    The rescan retries a refusal on the stream's backoff; an operator's retry
    makes it due at once.
    """

    pull.source.fields.update(ada={"fav_number": "seven"})
    _register(settings, CountedNumberMapping)
    pull.reopen()
    assert len(pull.sync().failure_ids) == 1
    (refusal,) = _refusals(pull, "ada")

    def reads_words(self: Any, row: Mapping[str, Any]) -> dict[str, Any]:
        return {"favourite_number": {"seven": 7}.get(row.get("fav_number"), row.get("fav_number"))}

    monkeypatch.setattr(CountedNumberMapping, "project", reads_words)
    pull.link("ada").retry_failure(refusal.slot)
    begin_stream_cycle(pull.stream, pull.backend)

    assert _person(pull, "ada").favourite_number == 7
    assert not _refusals(pull, "ada")


def test_a_mapping_bug_in_its_projection_fails_the_page_with_its_error(pull: Pull, settings: Any) -> None:
    """Only ``ValueError`` refuses a record; any other error is a bug, raised with its traceback."""

    pull.source.fields.update(ada={"fav_number": 7})
    _register(settings, BrokenProjectionMapping)
    pull.reopen()

    with pytest.raises(TypeError):
        pull.sync()


def test_a_mapping_bug_in_its_apply_record_fails_the_page_with_its_error(pull: Pull, settings: Any) -> None:
    pull.source.fields.update(ada={"fav_number": 7})
    _register(settings, BrokenApplyMapping)
    pull.reopen()

    with pytest.raises(TypeError):
        pull.sync()

    # The page rolled back whole: no record of it landed.
    assert not RecordLink.objects.filter(stream=pull.stream).exists()


def test_a_mapping_stating_a_group_it_does_not_declare_fails_the_page(pull: Pull, settings: Any) -> None:
    """Withdrawing the undeclared group at once would hide the mislabel: the apply raises instead."""

    _colours(pull, ada="green")
    _register(settings, MislabelledMapping)
    pull.reopen()

    with pytest.raises(ImproperlyConfigured, match="colour"):
        pull.sync()


def test_the_ingest_states_exactly_the_groups_parties_declares_on_a_person(pull: Pull) -> None:
    """The ingest's groups are read from parties' own declarations, so the list cannot drift from the ingest."""

    pull.sync()

    link = pull.link("ada")
    stated = {
        aspect
        for model in (Party, Person)
        for aspect in model.statements.filter(subject_id=link.target_object_id, link=link)
        .current()
        .values_list("aspect", flat=True)
    }
    assert stated == Person.contact_aspects()


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
    # A mapping declares the person groups it states, so its removal can withdraw them; one that states none
    # (it refines the contact, or writes pages, tags or a preferred mark) declares none, and stating a group
    # it does not declare fails its page instead.
    assert ids(UndeclaredMapping) == []
    assert ids(UnknownGroupMapping) == ["parties.E003"]
    # A mapping refines only the contact fields open to it, and no two mappings of one backend refine one field.
    assert ids(PhotoRefiningMapping) == ["parties.E005"]
    assert ids(HouseNameMapping, AnotherHouseNameMapping) == ["parties.E006"]
    assert ids(HouseNameMapping, ElsewhereRefiningMapping) == ["parties.E001"]
    # The contact ingest's own groups are its; a mapping stating one would overwrite the ingest's statement.
    assert ids(NameMapping) == ["parties.E004"]
    # A mapping claims the one key it is registered under, so one mapping registered twice fails.
    settings.ANGEE_DIRECTORY_MAPPING_CLASSES = {"colour": _path(ColourMapping), "colour_again": _path(ColourMapping)}
    assert [error.id for error in check_directory_mappings()] == ["angee.E004"]
