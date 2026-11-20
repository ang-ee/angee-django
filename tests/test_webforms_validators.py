"""A web form's stored definition and the answers it admits.

The definition is a checked subset of a SurveyJS survey definition; the answers
are judged by a schema derived from it. Every row names the rule it pins.
"""

from __future__ import annotations

import copy
import json
import time
from typing import Any

import pytest
from django.core.exceptions import ValidationError

from angee.webforms.validators import (
    answers_schema,
    missing_translations,
    questions,
    validate_answers,
    validate_definition,
    validate_languages,
)

LANGUAGES = ("en", "es")
FORM: dict[str, Any] = {
    "title": {"default": "Tell us", "es": "Cuéntanos"},
    "completedHtml": {"default": "<p>Thank you.</p>", "es": "<p>Gracias.</p>"},
    "pages": [
        {
            "name": "about",
            "elements": [
                {
                    "type": "text", "name": "name", "isRequired": True, "maxLength": 80,
                    "title": {"default": "Your name", "es": "Tu nombre"},
                },
                {"type": "text", "name": "email", "inputType": "email", "isRequired": True},
                {"type": "comment", "name": "idea", "maxLength": 500},
                {
                    "type": "checkbox", "name": "topics", "isRequired": True, "showOtherItem": True,
                    "maxSelectedChoices": 2,
                    "choices": [
                        {"value": "music", "text": {"default": "Music", "es": "Música"}},
                        {"value": "talks"},
                        {"value": "film"},
                    ],
                },
                {
                    "type": "radiogroup", "name": "size", "showOtherItem": True,
                    "choices": [{"value": "small"}, {"value": "large"}],
                },
            ],
        },
        {
            "name": "visit",
            "elements": [
                {
                    "type": "panel", "name": "more",
                    "elements": [
                        {"type": "boolean", "name": "again"},
                        {"type": "rating", "name": "score", "rateMax": 10},
                        {"type": "text", "name": "guests", "inputType": "number", "min": 0, "max": 6},
                        {"type": "text", "name": "day", "inputType": "date", "min": "2030-01-01", "max": "2030-12-31"},
                        {"type": "text", "name": "site", "inputType": "url"},
                        {
                            "type": "html", "name": "notice",
                            "html": {"default": "<p>Kept for a year.</p>", "es": "<p>Se guarda un año.</p>"},
                        },
                    ],
                },
            ],
        },
    ],
}
FIRST = ("pages", 0, "elements")
PANEL = ("pages", 1, "elements", 0, "elements")
ANSWERS: dict[str, Any] = {
    "name": "Ada Example", "email": "ada@example.test", "idea": "An evening of readings.",
    "topics": ["music", "other"], "topics-Comment": "Dance", "size": "small", "again": True, "score": 9,
    "guests": 2, "day": "2030-06-01", "site": "https://example.test/visit",
}


def changed(path: tuple[Any, ...], value: Any, *, source: dict[str, Any] = FORM) -> dict[str, Any]:
    """Return a copy of the form with the node at ``path`` replaced; a missing last key is added."""

    form = copy.deepcopy(source)
    node: Any = form
    for part in path[:-1]:
        node = node[part]
    node[path[-1]] = value
    return form


def refusal(definition: Any, languages: tuple[str, ...] = LANGUAGES, default: str = "en") -> list[str]:
    with pytest.raises(ValidationError) as refused:
        validate_definition(definition, languages=languages, default_language=default)
    return [str(message) for message in refused.value.message_dict["definition"]]


def test_a_definition_inside_the_subset_is_admitted_and_its_questions_keep_their_order() -> None:
    validate_definition(FORM, languages=LANGUAGES, default_language="en")

    names = [question["name"] for question in questions(FORM)]
    assert names == ["name", "email", "idea", "topics", "size", "again", "score", "guests", "day", "site"]


@pytest.mark.parametrize(("definition", "where"), [
    pytest.param(changed((*FIRST, 0, "type"), "matrix"), "$.pages[0].elements[0].type", id="a-type-outside-the-subset"),
    pytest.param(changed((*FIRST, 0, "visibleIf"), "{email} notempty"), "$.pages[0].elements[0]", id="an-expression"),
    pytest.param(changed(("showProgressBar",), "top"), "$", id="a-presentation-switch"),
    pytest.param(changed((*FIRST, 3, "choices", 1), "talks"), "$.pages[0].elements[3].choices[1]", id="a-bare-choice"),
    pytest.param(
        changed((*FIRST, 4, "choices", 0, "value"), "other"), "$.pages[0].elements[4].choices[0].value",
        id="a-choice-named-as-the-other-answer",
    ),
    pytest.param(changed((*FIRST, 2, "name"), "the-idea"), "$.pages[0].elements[2].name", id="a-name-with-a-dash"),
    pytest.param(changed(("title",), {"es": "Cuéntanos"}), "$.title", id="a-text-map-without-its-default"),
    pytest.param(changed((*FIRST, 0, "maxLength"), 0), "$.pages[0].elements[0].maxLength", id="a-length-of-nothing"),
    pytest.param(changed(("pages",), []), "$.pages", id="no-page"),
    pytest.param({**FORM, "elements": FORM["pages"][0]["elements"]}, "$", id="pages-and-loose-elements-together"),
])
def test_a_definition_outside_the_vocabulary_is_refused_at_the_node_that_breaks_it(definition: Any, where: str) -> None:
    messages = refusal(definition)

    assert any(message.startswith(f"{where}:") for message in messages), messages


@pytest.mark.parametrize(("definition", "where"), [
    pytest.param(changed((*FIRST, 1, "name"), "name"), "/pages/0/elements/1", id="a-name-used-twice"),
    pytest.param(changed(("pages", 1, "name"), "about"), "/pages/1", id="a-page-name-used-twice"),
    pytest.param(
        changed((*FIRST, 3, "choices", 2, "value"), "music"), "/pages/0/elements/3", id="a-choice-value-used-twice",
    ),
    pytest.param(changed((*FIRST, 3, "minSelectedChoices"), 3), "/pages/0/elements/3", id="more-to-select-than-may-be"),
    pytest.param(changed((*PANEL, 1, "rateMin"), 10), "/pages/1/elements/0/elements/1", id="a-rating-with-no-range"),
    pytest.param(changed((*FIRST, 0, "min"), 1), "/pages/0/elements/0", id="bounds-on-plain-text"),
    pytest.param(changed((*PANEL, 2, "min"), 9), "/pages/1/elements/0/elements/2", id="number-bounds-reversed"),
    pytest.param(changed((*PANEL, 3, "max"), "next year"), "/pages/1/elements/0/elements/3", id="a-bound-not-a-date"),
    pytest.param(changed(("title",), {"default": "Tell us", "fr": "Dites-nous"}), "/title", id="not-a-form-language"),
    pytest.param(changed(("title",), {"default": "Tell us", "en": "Tell us"}), "/title", id="default-under-its-tag"),
])
def test_a_definition_whose_parts_disagree_is_refused_at_the_part(definition: Any, where: str) -> None:
    messages = refusal(definition)

    assert any(message.startswith(f"{where}:") for message in messages), messages


def test_a_form_asks_at_least_one_question_and_panels_nest_only_so_deep() -> None:
    only_text = {"elements": [{"type": "html", "name": "notice", "html": "Closed."}]}
    deep: dict[str, Any] = {"type": "text", "name": "q"}
    for level in range(4):
        deep = {"type": "panel", "name": f"p{level}", "elements": [deep]}

    assert refusal(only_text) == ["A form asks at least one question."]
    assert any("nest at most" in message for message in refusal({"elements": [deep]}))


@pytest.mark.parametrize(("languages", "default", "field"), [
    pytest.param((), "en", "languages", id="no-language"),
    pytest.param(("en", "Spanish"), "en", "languages", id="not-a-tag"),
    pytest.param(("en", "en"), "en", "languages", id="listed-twice"),
    pytest.param(("en", "es"), "fr", "default_language", id="a-default-the-form-is-not-written-in"),
])
def test_a_forms_languages_are_tags_listed_once_and_hold_its_default(
    languages: tuple[str, ...], default: str, field: str,
) -> None:
    with pytest.raises(ValidationError) as refused:
        validate_languages(languages, default)

    assert field in refused.value.message_dict


def test_a_text_map_lacking_a_language_is_missing_and_one_string_never_is() -> None:
    assert missing_translations(FORM, languages=LANGUAGES, default_language="en") == []

    untranslated = changed(("title",), {"default": "Tell us"})
    blank = changed((*FIRST, 0, "title"), {"default": "Your name", "es": "  "})

    assert missing_translations(untranslated, languages=LANGUAGES, default_language="en") == ["/title"]
    assert missing_translations(blank, languages=LANGUAGES, default_language="en") == ["/pages/0/elements/0/title"]
    assert missing_translations(untranslated, languages=("en",), default_language="en") == []


def test_answers_inside_the_definition_are_admitted_whole_or_with_only_what_is_required() -> None:
    assert validate_answers(FORM, ANSWERS) == ANSWERS

    least = {"name": "Ada Example", "email": "ada@example.test", "topics": ["film"]}

    assert validate_answers(FORM, least) == least


def without(*names: str, **replaced: Any) -> dict[str, Any]:
    kept = {name: value for name, value in ANSWERS.items() if name not in names}
    return {**kept, **{name.replace("__", "-"): value for name, value in replaced.items()}}


@pytest.mark.parametrize(("answers", "question"), [
    pytest.param(without("name"), "name", id="a-required-answer-left-out"),
    pytest.param(without(name="   "), "name", id="a-required-answer-of-spaces"),
    pytest.param(without(name="x" * 81), "name", id="longer-than-the-question-allows"),
    pytest.param(without(email="ada at example"), "email", id="not-an-email-address"),
    pytest.param(without(site="javascript:alert(1)"), "site", id="not-a-web-address"),
    pytest.param(without(idea={"record": "usr_1"}), "idea", id="an-object-where-text-is-asked"),
    pytest.param(without(size="medium"), "size", id="a-choice-never-offered"),
    pytest.param(without(size="other"), "size-Comment", id="other-without-its-text"),
    pytest.param(without(size__Comment="Huge"), "size-Comment", id="other-text-without-other-chosen"),
    pytest.param(without("topics-Comment"), "topics-Comment", id="other-among-several-without-its-text"),
    pytest.param(without("topics-Comment", topics=["music", "talks", "film"]), "topics", id="more-chosen-than-allowed"),
    pytest.param(without("topics-Comment", topics=["music", "music"]), "topics", id="one-choice-chosen-twice"),
    pytest.param(without("topics-Comment", topics=[]), "topics", id="a-required-choice-of-none"),
    pytest.param(without("topics-Comment", topics="music"), "topics", id="one-code-where-a-list-is-asked"),
    pytest.param(without(again="yes"), "again", id="a-word-where-yes-or-no-is-asked"),
    pytest.param(without(score=11), "score", id="a-rating-off-the-scale"),
    pytest.param(without(score=9.5), "score", id="a-rating-between-two-marks"),
    pytest.param(without(guests=7), "guests", id="a-number-past-its-bound"),
    pytest.param(without(guests="2"), "guests", id="a-number-sent-as-text"),
    pytest.param(without(guests=True), "guests", id="a-boolean-where-a-number-is-asked"),
    pytest.param(without(day="1 June"), "day", id="not-a-date"),
    pytest.param(without(day="2031-01-01"), "day", id="a-date-past-its-bound"),
    pytest.param(without(notice="read"), "notice", id="an-answer-to-what-is-not-a-question"),
    pytest.param(without(owner="usr_1"), "owner", id="an-answer-to-a-question-never-asked"),
])
def test_an_answer_the_definition_does_not_admit_is_refused_under_its_questions_name(
    answers: dict[str, Any], question: str,
) -> None:
    with pytest.raises(ValidationError) as refused:
        validate_answers(FORM, answers)

    assert set(refused.value.message_dict) == {question}


def test_answers_that_are_not_an_object_are_refused_whole() -> None:
    with pytest.raises(ValidationError) as refused:
        validate_answers(FORM, ["name", "Ada Example"])

    assert set(refused.value.message_dict) == {"__all__"}


def test_the_answers_schema_names_each_question_once_and_nothing_else() -> None:
    schema = answers_schema(FORM)

    assert set(schema["properties"]) == {question["name"] for question in questions(FORM)} | {
        "topics-Comment", "size-Comment",
    }
    assert (schema["required"], schema["additionalProperties"]) == (["name", "email", "topics"], False)


def test_a_list_longer_than_the_choices_offered_is_refused_without_being_read() -> None:
    """A body under the endpoint's cap cannot buy seconds of comparison: the list is bounded before the schema."""

    items: list[Any] = []
    while len(json.dumps({"answers": {"topics": items}})) < 65_000:
        items.extend([len(items), f"s{len(items)}"])
    started = time.perf_counter()
    with pytest.raises(ValidationError) as refused:
        validate_answers(FORM, without("topics-Comment", topics=items))
    elapsed = time.perf_counter() - started

    assert refused.value.message_dict == {"topics": ["Choose at most 4."]}
    assert elapsed < 0.5, f"{len(items)} items took {elapsed:.2f}s"


def test_a_character_the_database_cannot_store_is_refused_under_its_question() -> None:
    with pytest.raises(ValidationError) as refused:
        validate_answers(FORM, without(idea="An evening\x00 of readings.", size__Comment="x\x00"))

    assert "idea" in refused.value.message_dict and "size-Comment" in refused.value.message_dict


def test_a_multi_select_is_bounded_by_what_it_offers_in_the_derived_schema() -> None:
    topics = answers_schema(FORM)["properties"]["topics"]

    assert (topics["maxItems"], topics["uniqueItems"]) == (2, True), "the tighter of the choices offered and the cap"

