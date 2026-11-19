"""A person's languages and occupation: their own statement groups, the language field and the GraphQL surface."""

from __future__ import annotations

from typing import Any

import pytest
from django.core.exceptions import ValidationError
from rebac import system_context

from angee.graphql.testing.documents import execute_schema
from angee.graphql.testing.documents import result_data as _data
from angee.messaging.testing.models import Person
from angee.parties.fields import LanguageCodesField
from tests.conftest import create_platform_admin
from tests.test_parties_graphql import _schema


def test_languages_are_iso_639_codes_most_preferred_first_each_once() -> None:
    """A three-letter code of a language with a two-letter one is stored as that; case and repeats do not count."""

    field = LanguageCodesField(default=list, blank=True)

    assert field.clean(["ES", "eng", "spa", " en "], None) == ["es", "en"]
    # A language without a two-letter code keeps its ISO 639-3 code.
    assert field.clean(["yue"], None) == ["yue"]
    assert field.clean([], None) == []
    for refused in (["en-GB"], ["klingon"], ["xx"], "en", [1], {"en": 1}):
        with pytest.raises(ValidationError):
            field.clean(refused, None)


def _statements(person: Any, aspect: str) -> list[Any]:
    with system_context(reason="tests.parties.person_facts.statements"):
        return list(Person.statements.filter(subject_id=person.pk, aspect=aspect).current())


def test_a_persons_edit_states_languages_and_occupation_each_in_its_own_group(db: None) -> None:
    """Editing the occupation alone states the occupation group and leaves the languages as they were stated."""

    admin = create_platform_admin("person-facts-admin")
    with system_context(reason="tests.parties.person_facts.seed"):
        person = Person.objects.create(display_name="Ada Example", created_by_id=admin.pk)
    person = Person.objects.with_actor(admin).get(pk=person.pk)

    person.languages = ["spa", "EN"]
    person.occupation = "Cartographer"
    person.save()

    person.refresh_from_db()
    assert (person.languages, person.occupation) == (["es", "en"], "Cartographer")
    (languages,) = _statements(person, "languages")
    (occupation,) = _statements(person, "occupation")
    assert (languages.source_kind, languages.languages) == ("person", ["es", "en"])
    assert (occupation.source_kind, occupation.occupation) == ("person", "Cartographer")

    person.occupation = "Surveyor"
    person.save(update_fields=["occupation", "updated_at"])

    assert _statements(person, "languages") == [languages]
    (restated,) = _statements(person, "occupation")
    assert restated.occupation == "Surveyor"


def test_the_people_resource_writes_languages_and_occupation(db: None) -> None:
    """Both are person fields of the people resource; the language list is a list the form edits as tags."""

    admin = create_platform_admin("person-facts-hasura-admin")
    schema = _schema("public")
    metadata = {item.model_label: item for item in schema.angee_resources}["parties.Person"]
    fields = {field.name: field for field in metadata.fields}
    assert (fields["languages"].kind, fields["languages"].widget) == ("list", "tagInput")
    assert {"languages", "occupation"} <= set(metadata.create_fields)

    created = _data(
        execute_schema(
            schema,
            """
            mutation CreatePerson {
              insert_people_one(object: {display_name: "Ada", languages: ["es", "eng"], occupation: "Cartographer"}) {
                id languages occupation
              }
            }
            """,
            user=admin,
        )
    )["insert_people_one"]
    assert (created["languages"], created["occupation"]) == (["es", "en"], "Cartographer")

    updated = _data(
        execute_schema(
            schema,
            """
            mutation UpdatePerson($id: String!) {
              update_people_by_pk(pk_columns: {id: $id}, _set: {languages: ["fr"], occupation: ""}) {
                languages occupation
              }
            }
            """,
            {"id": created["id"]},
            user=admin,
        )
    )["update_people_by_pk"]
    assert updated == {"languages": ["fr"], "occupation": ""}

    refused = execute_schema(
        schema,
        """
        mutation UpdatePerson($id: String!) {
          update_people_by_pk(pk_columns: {id: $id}, _set: {languages: ["en-GB"]}) { languages }
        }
        """,
        {"id": created["id"]},
        user=admin,
    )
    assert refused.errors
    with system_context(reason="tests.parties.person_facts.verify"):
        assert Person.objects.get(sqid=created["id"]).languages == ["fr"]
