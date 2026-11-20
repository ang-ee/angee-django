"""A web form's stored definition and the answers it admits.

The stored definition is a checked subset of a SurveyJS survey definition. The
schema file beside this module (``schemas/definition.schema.json``) owns the
vocabulary: which question types and properties exist. This module owns what a
schema cannot say (a name used once, the languages a text may carry, bounds that
agree) and derives, from one definition, the JSON Schema of the answers the
server accepts. An anonymous submission is judged by that derived schema, so an
answer is only ever a coded choice, a bounded string, a number or a boolean: it
can never name a record or carry an instruction.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Sequence
from datetime import date
from functools import cache
from html import escape
from html.parser import HTMLParser
from importlib.resources import files
from typing import TYPE_CHECKING, Any

from django.core.exceptions import ValidationError
from django.core.validators import EmailValidator, URLValidator

from angee.base.validators import validation_issues, validator

if TYPE_CHECKING:
    from jsonschema import Draft202012Validator

JSON_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"

OTHER = "other"
"""The answer a choice question stores when its respondent chose "Other"."""

OTHER_SUFFIX = "-Comment"
"""What follows a question's name in the answer that holds its "Other" text."""

QUESTION_TYPES = frozenset({"text", "comment", "radiogroup", "dropdown", "checkbox", "boolean", "rating"})
TEXT_KEYS = frozenset({
    "title", "description", "completedHtml", "completeText", "pagePrevText", "pageNextText", "requiredErrorText",
    "placeholder", "otherText", "otherPlaceholder", "labelTrue", "labelFalse", "minRateDescription",
    "maxRateDescription", "html", "text",
})
LANGUAGE_TAG = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})*$")
SUBMISSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
LANGUAGE_TAG_LENGTH = 35
MARKUP_KEYS = frozenset({"html", "completedHtml"})
"""The only texts that are markup. Every other text is plain and is shown as written."""

MARKUP_TAGS = frozenset({
    "p", "br", "strong", "em", "b", "i", "ul", "ol", "li", "h1", "h2", "h3", "h4", "h5", "h6", "a",
})
MARKUP_LINKS = ("http://", "https://", "mailto:")
MAX_QUESTIONS = 200
TEXT_LIMIT = 2_000
COMMENT_LIMIT = 20_000
DEFAULT_RATING = (1, 5)

_validate_email = EmailValidator()
_validate_url = URLValidator(schemes=("http", "https"))


@cache
def definition_schema() -> dict[str, Any]:
    """Return the JSON Schema of a stored definition; callers read it and never change it."""

    document = files(__package__).joinpath("schemas", "definition.schema.json").read_text(encoding="utf-8")
    return dict(json.loads(document))


@cache
def _definition_validator() -> Draft202012Validator:
    return validator(definition_schema())


def validate_submission_id(value: Any) -> str:
    """Return the id a client chose for one submission, the key that makes a resend the same response."""

    if not isinstance(value, str) or not SUBMISSION_ID.fullmatch(value):
        raise ValidationError({"submission_id": "Use 1-128 letters, numbers, dots, underscores, colons, or hyphens."})
    return value


def validate_languages(languages: Sequence[str], default_language: str) -> None:
    """Refuse a language list that is empty, repeats a tag, holds a malformed one or lacks the default."""

    errors: dict[str, list[str]] = {}
    if not isinstance(languages, (list, tuple)):
        raise ValidationError({"languages": ["A form's languages are a list of language tags."]})
    bad = [
        tag for tag in languages
        if not isinstance(tag, str) or not LANGUAGE_TAG.fullmatch(tag) or len(tag) > LANGUAGE_TAG_LENGTH
    ]
    if not languages:
        errors["languages"] = ["A form is written in at least one language."]
    elif bad:
        errors["languages"] = [f"Not a language tag: {', '.join(map(repr, bad))}."]
    elif len(set(languages)) != len(languages):
        errors["languages"] = ["A language is listed once."]
    if default_language not in languages:
        errors["default_language"] = ["The default language is one of the form's languages."]
    if errors:
        raise ValidationError(errors)


def validate_definition(definition: Any, *, languages: Sequence[str], default_language: str) -> None:
    """Refuse a definition outside the stored subset, or one whose parts disagree.

    ``languages`` are the tags the form is written in and ``default_language``
    the one a text map holds under ``default``; a map names no other language.
    """

    validate_languages(languages, default_language)
    errors = [f"{error.json_path}: {error.message}" for error in _definition_validator().iter_errors(definition)]
    if not errors:
        errors = list(_disagreements(definition, [tag for tag in languages if tag != default_language]))
    if errors:
        raise ValidationError({"definition": errors[:50]})


class _Markup(HTMLParser):
    """Read stored markup and write it back in its one canonical spelling.

    Markup a form stores is rendered as HTML on a public page of the
    application's origin. It is admitted only when it is made of the allowed
    tags, a link's ``href`` and text, AND equals the canonical text rebuilt from
    what this parser read: then there is nothing a browser could read
    differently from what was judged. It is a refusal, never a rewrite.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rebuilt: list[str] = []
        self.unclosed: list[str] = []
        self.refusal = ""

    def refuse(self, reason: str) -> None:
        self.refusal = self.refusal or reason

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in MARKUP_TAGS:
            return self.refuse(f"<{tag}> is not allowed")
        if tag != "a":
            if attrs:
                return self.refuse(f"<{tag}> carries no attribute")
            self.rebuilt.append(f"<{tag}>")
        else:
            if len(attrs) != 1 or attrs[0][0] != "href" or attrs[0][1] is None:
                return self.refuse("a link carries an href and nothing else")
            # The parser has decoded the value, so a scheme split by an encoded character is seen whole.
            address = attrs[0][1]
            if (
                address != address.strip()
                or not address.lower().startswith(MARKUP_LINKS)
                or any(character.isspace() or ord(character) < 0x20 for character in address)
            ):
                return self.refuse("a link goes to an http, https or mailto address")
            self.rebuilt.append(f'<a href="{escape(address, quote=True)}">')
        if tag != "br":
            self.unclosed.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.refuse(f"write <{tag}> without a closing slash")

    def handle_endtag(self, tag: str) -> None:
        if not self.unclosed or self.unclosed[-1] != tag:
            return self.refuse(f"</{tag}> closes nothing that is open")
        self.unclosed.pop()
        self.rebuilt.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        self.rebuilt.append(escape(data, quote=False))

    def handle_comment(self, data: str) -> None:
        self.refuse("comments are not allowed")

    def handle_decl(self, decl: str) -> None:
        self.refuse("declarations are not allowed")

    def handle_pi(self, data: str) -> None:
        self.refuse("processing instructions are not allowed")

    def unknown_decl(self, data: str) -> None:
        self.refuse("declarations are not allowed")


def markup_refusal(value: str) -> str:
    """Return why ``value`` is not the simple markup a form may store, or an empty string."""

    reader = _Markup()
    reader.feed(value)
    reader.close()
    if reader.refusal:
        return reader.refusal
    if reader.unclosed:
        return f"<{reader.unclosed[-1]}> is never closed"
    if "".join(reader.rebuilt) != value:
        return (
            "write it plainly: lower-case tags without spaces, a double-quoted href, "
            "and &amp;, &lt; and &gt; for those characters in text"
        )
    return ""


def elements(definition: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any], int]]:
    """Yield every element with its JSON pointer and panel depth, in the order a respondent meets them."""

    def walk(items: list[dict[str, Any]], pointer: str, depth: int) -> Iterator[tuple[str, dict[str, Any], int]]:
        for index, element in enumerate(items):
            here = f"{pointer}/{index}"
            yield here, element, depth
            if element.get("type") == "panel":
                yield from walk(element.get("elements", []), f"{here}/elements", depth + 1)

    for index, page in enumerate(definition.get("pages", [])):
        yield from walk(page.get("elements", []), f"/pages/{index}/elements", 0)
    yield from walk(definition.get("elements", []), "/elements", 0)


def questions(definition: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the elements that take an answer, in order."""

    return [element for _pointer, element, _depth in elements(definition) if element.get("type") in QUESTION_TYPES]


def texts(definition: Any, pointer: str = "") -> Iterator[tuple[str, Any]]:
    """Yield every text of a definition with its JSON pointer: a string, or a map of languages."""

    if isinstance(definition, dict):
        for key, value in definition.items():
            here = f"{pointer}/{key}"
            if key in TEXT_KEYS and isinstance(value, (str, dict)):
                yield here, value
            else:
                yield from texts(value, here)
    elif isinstance(definition, list):
        for index, value in enumerate(definition):
            yield from texts(value, f"{pointer}/{index}")


def missing_translations(definition: dict[str, Any], *, languages: Sequence[str], default_language: str) -> list[str]:
    """Return the pointers of the text maps that lack one of the form's other languages.

    A text written as one string is the same in every language and is never
    missing. An empty translation counts as missing.
    """

    others = [tag for tag in languages if tag != default_language]
    return [
        pointer for pointer, value in texts(definition)
        if isinstance(value, dict) and any(not value.get(tag, "").strip() for tag in others)
    ]


def answers_schema(definition: dict[str, Any]) -> dict[str, Any]:
    """Return the JSON Schema of the answers a valid definition admits.

    One property per question, by its name, and nothing else. A choice is one
    of its codes; "Other", where offered, is the code ``other`` with its text in
    ``<name>-Comment``, required then and refused otherwise.
    """

    properties: dict[str, Any] = {}
    required: list[str] = []
    rules: list[dict[str, Any]] = []
    for question in questions(definition):
        name = question["name"]
        properties[name] = _answer_schema(question)
        if question.get("isRequired"):
            required.append(name)
        if question.get("showOtherItem"):
            comment = f"{name}{OTHER_SUFFIX}"
            properties[comment] = {"type": "string", "minLength": 1, "maxLength": TEXT_LIMIT}
            chose = (
                {"type": "array", "contains": {"const": OTHER}} if question["type"] == "checkbox" else {"const": OTHER}
            )
            # ``{"not": {}}`` and not ``false``: a refusal by a boolean schema carries no path to its property.
            rules.append({
                "if": {"required": [name], "properties": {name: chose}},
                "then": {"required": [comment]},
                "else": {"properties": {comment: {"not": {}}}},
            })
    schema: dict[str, Any] = {
        "$schema": JSON_SCHEMA_DIALECT, "type": "object", "properties": properties,
        "required": required, "additionalProperties": False,
    }
    if rules:
        schema["allOf"] = rules
    return schema


def validate_answers(definition: dict[str, Any], answers: Any) -> dict[str, Any]:
    """Return ``answers`` when the definition admits them; raise each refusal under its question's name."""

    if not isinstance(answers, dict):
        raise ValidationError({"__all__": ["Answers are an object keyed by question name."]})
    asked = questions(definition)
    # Refused before the schema reads them: every keyword of a schema runs whatever another found,
    # and comparing a long list's items pairwise costs seconds within the body cap.
    oversized = {
        question["name"]: _offered(question) for question in asked
        if isinstance(answers.get(question["name"]), list) and len(answers[question["name"]]) > _offered(question)
    }
    unreadable = [name for name, value in answers.items() if isinstance(value, str) and "\x00" in value]
    judged = {name: value for name, value in answers.items() if name not in oversized}
    issues = validation_issues(answers_schema(definition), judged)
    for name, most in oversized.items():
        issues[name] = [f"Choose at most {most}."]
    for name in unreadable:
        issues.setdefault(name, ["Remove the character that cannot be stored."])
    for question in asked:
        name = question["name"]
        comment = f"{name}{OTHER_SUFFIX}"
        if comment in issues and comment not in answers:
            issues[comment] = ["Say what the other answer is."]
        elif comment in issues and not _chose_other(answers.get(name)):
            issues[comment] = ["Leave this out unless the other choice is taken."]
        if name in issues or name not in answers:
            continue
        refusal = _refusal(question, answers[name])
        if refusal:
            issues[name] = [refusal]
    if issues:
        raise ValidationError(issues)
    return dict(answers)


def _offered(question: dict[str, Any]) -> int:
    """How many choices a question offers, its "Other" included; none for a question without choices."""

    return len(question.get("choices", ())) + (1 if question.get("showOtherItem") else 0)


def _chose_other(value: Any) -> bool:
    return value == OTHER or (isinstance(value, list) and OTHER in value)


def _answer_schema(question: dict[str, Any]) -> dict[str, Any]:
    kind = question["type"]
    needed = bool(question.get("isRequired"))
    if kind in ("radiogroup", "dropdown", "checkbox"):
        codes = [choice["value"] for choice in question["choices"]]
        if question.get("showOtherItem"):
            codes.append(OTHER)
        if kind != "checkbox":
            return {"enum": codes}
        chosen: dict[str, Any] = {
            "type": "array", "uniqueItems": True, "items": {"enum": codes},
            "maxItems": min(question.get("maxSelectedChoices", len(codes)), len(codes)),
        }
        least = max(question.get("minSelectedChoices", 0), 1 if needed else 0)
        if least:
            chosen["minItems"] = least
        return chosen
    if kind == "boolean":
        return {"type": "boolean"}
    if kind == "rating":
        return {
            "type": "integer", "minimum": question.get("rateMin", DEFAULT_RATING[0]),
            "maximum": question.get("rateMax", DEFAULT_RATING[1]),
        }
    entry = question.get("inputType", "text") if kind == "text" else "comment"
    if entry == "number":
        number: dict[str, Any] = {"type": "number"}
        for bound, keyword in (("min", "minimum"), ("max", "maximum")):
            if bound in question:
                number[keyword] = question[bound]
        return number
    limit = question.get("maxLength", COMMENT_LIMIT if entry == "comment" else TEXT_LIMIT)
    written: dict[str, Any] = {"type": "string", "maxLength": limit}
    if needed:
        written["minLength"] = 1
    if entry == "date":
        written["format"] = "date"
    return written


def _refusal(question: dict[str, Any], value: Any) -> str:
    """Return why a structurally valid answer is still refused, or an empty string."""

    if not isinstance(value, str):
        return ""
    if question.get("isRequired") and not value.strip():
        return "This question needs an answer."
    entry = question.get("inputType", "text") if question["type"] == "text" else ""
    try:
        if entry == "email" and value:
            _validate_email(value)
        elif entry == "url" and value:
            _validate_url(value)
    except ValidationError:
        return "Enter a valid email address." if entry == "email" else "Enter a valid web address."
    if entry == "date" and value:
        day = date.fromisoformat(value)
        if "min" in question and day < date.fromisoformat(question["min"]):
            return f"Enter a date on or after {question['min']}."
        if "max" in question and day > date.fromisoformat(question["max"]):
            return f"Enter a date on or before {question['max']}."
    return ""


def _disagreements(definition: dict[str, Any], other_languages: Sequence[str]) -> Iterator[str]:
    """Yield what the schema cannot say about a definition it accepts."""

    names: set[str] = set()
    for index, page in enumerate(definition.get("pages", [])):
        if page["name"] in names:
            yield f"/pages/{index}: the name {page['name']!r} is used twice."
        names.add(page["name"])
    count = 0
    for pointer, element, _depth in elements(definition):
        name = element["name"]
        if name in names:
            yield f"{pointer}: the name {name!r} is used twice."
        names.add(name)
        if element["type"] in QUESTION_TYPES:
            count += 1
            yield from (f"{pointer}: {problem}" for problem in _question_disagreements(element))
    if not count:
        yield "A form asks at least one question."
    if count > MAX_QUESTIONS:
        yield f"A form asks at most {MAX_QUESTIONS} questions."
    allowed = {"default", *other_languages}
    for pointer, value in texts(definition):
        if pointer.rsplit("/", 1)[-1] in MARKUP_KEYS:
            written = value.items() if isinstance(value, dict) else (("", value),)
            for language, markup in written:
                refusal = markup_refusal(markup)
                if refusal:
                    where = f"{pointer}/{language}" if language else pointer
                    yield (
                        f"{where}: {refusal}. Only paragraphs, line breaks, emphasis, lists, headings and "
                        "links are stored here."
                    )
        if isinstance(value, dict):
            unknown = sorted(set(value) - allowed)
            if unknown:
                yield (
                    f"{pointer}: {', '.join(unknown)} is not one of the form's other languages "
                    "(the default language is written under 'default')."
                )


def _question_disagreements(question: dict[str, Any]) -> Iterator[str]:
    kind = question["type"]
    if "choices" in question:
        codes = [choice["value"] for choice in question["choices"]]
        if len(set(codes)) != len(codes):
            yield "a choice's value is used once."
        offered = len(codes) + (1 if question.get("showOtherItem") else 0)
        least, most = question.get("minSelectedChoices", 0), question.get("maxSelectedChoices", offered)
        if least > most or least > offered:
            yield "the fewest choices to select is no more than the most, nor than the choices offered."
    if kind == "rating":
        low, high = question.get("rateMin", DEFAULT_RATING[0]), question.get("rateMax", DEFAULT_RATING[1])
        if low >= high:
            yield "a rating's lowest value is below its highest."
    if kind == "text" and ("min" in question or "max" in question):
        entry = question.get("inputType", "text")
        bounds = [question[bound] for bound in ("min", "max") if bound in question]
        if entry == "number":
            if any(isinstance(bound, bool) or not isinstance(bound, (int, float)) for bound in bounds):
                yield "a number's bounds are numbers."
            elif len(bounds) == 2 and bounds[0] > bounds[1]:
                yield "the lowest number is no more than the highest."
        elif entry == "date":
            try:
                days = [date.fromisoformat(bound) for bound in bounds]
            except (TypeError, ValueError):
                yield "a date's bounds are dates written as YYYY-MM-DD."
            else:
                if len(days) == 2 and days[0] > days[1]:
                    yield "the earliest date is no later than the latest."
        else:
            yield "only a number or a date has bounds."
