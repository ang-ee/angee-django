"""The message templates console's story fixture is what the server says, recorded; fail here when it drifts.

``src/angee/messaging_templates/web/src/fixtures.recorded.json`` holds every value
the console's stories and native tests quote from the server: the console resource
metadata of ``messaging_templates.MessageTemplate`` and ``brandkit.BrandKit`` as a
host enabling the addon alone emits it, the library's rows as an administrator reads
them and what a member reads differently, the brand kits, and, for the rows the
stories open, their backlinks, previews and delete previews and what Override, Keep
override, the insert and a refused save answer, each with what it changed in the
library, what Keep override answers on an override another session deleted, two saves
the server keeps with what a reload then reads, and three previews of unsaved edits: one
that renders, one that saves but does not render with its sample, and the refused save's
own. The checks the console relies on are asserted as they are recorded: a pasted source
with carriage returns saves as newlines and leaves "Default updated" as it was, and an
override whose default changed still says so after a save and a reload.
Public ids are stable aliases (:func:`_alias`), also inside the server's words, and
timestamps fixed, so an unchanged server records the same file.

The test records it afresh and fails when the committed file differs. Re-record it,
never edit it, with ``ANGEE_RECORD_FIXTURES=1`` (the command is in ``docs/checks.md``),
whenever the console's schema, a shipped row or a server message changes.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from django.apps import apps
from django.db import transaction
from django_compose.components import enabled_apps
from rebac import system_context

from angee.base.models.identity import public_id_of
from angee.graphql.schema import GraphQLSchemas
from angee.graphql.testing.documents import execute_schema, result_data
from angee.messaging_templates.models import MessageTemplateKind
from angee.resources.testing.models import Resource
from tests.conftest import create_platform_admin, create_user

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "src/angee/messaging_templates/web/src/fixtures.recorded.json"
RECORD = os.environ.get("ANGEE_RECORD_FIXTURES") == "1"

MODEL = "messaging_templates.MessageTemplate"
KIT_MODEL = "brandkit.BrandKit"

_LAYOUT = MessageTemplateKind.LAYOUT.handler.defaults
"""A deployment's own layout's starting forms; the two extra shipped layouts are written from them."""

_SHIPPED_LAYOUTS: dict[str, dict[str, str]] = {
    "letter": {"name": "Letter", "description": "One plain column, for mail that reads as a letter."},
    "compact": {"name": "Compact", "description": "A narrow column without header or footer."},
}

_TEMPLATES: dict[str, dict[str, Any]] = {
    "example.welcome": {
        "name": "Welcome",
        "description": "Sent when a person's account is ready.",
        "subject": "Welcome to {{ brand.name }}",
        "preheader": "Your account is ready to use.",
        "source": (
            '{% include "parts/greeting" with person=person only %}\n\n'
            "Your {{ brand.name }} account is ready. Sign in to finish setting it up.\n\n"
            '{% include "blocks/button" with label="Sign in" url=sign_in_url only %}\n\n'
            '{% include "parts/ignore_notice" only %}\n\n'
            '{% include "parts/signature" only %}'
        ),
        "variables": {
            "person.first_name": "The recipient's first name; may be empty.",
            "sign_in_url": "Where the person signs in.",
        },
        "sample": {"person": {"first_name": "Ana"}, "sign_in_url": "https://app.example.test/sign-in"},
    },
    "example.password_reset": {
        "name": "Password reset",
        "description": "Sent when a person asks to choose a new password.",
        "subject": "Choose a new {{ brand.name }} password",
        "preheader": "The link works for one hour.",
        "source": (
            '{% include "parts/greeting" with person=person only %}\n\n'
            "Someone asked to reset the password of your {{ brand.name }} account.\n\n"
            '{% include "blocks/button" with label="Choose a new password" url=reset_url only %}\n\n'
            '{% include "parts/ignore_notice" only %}\n\n'
            '{% include "parts/signature" only %}'
        ),
        "variables": {
            "person.first_name": "The recipient's first name; may be empty.",
            "reset_url": "Where the person chooses a new password.",
        },
        "sample": {"person": {"first_name": "Ana"}, "reset_url": "https://app.example.test/reset/abc"},
    },
    "example.invoice_sent": {
        "name": "Invoice sent",
        "description": "Sent with each invoice.",
        "subject": "Invoice {{ invoice.number }} from {{ brand.name }}",
        "preheader": 'Due {{ invoice.due|date:"DATE_FORMAT" }}.',
        "source": (
            '{% include "parts/greeting" with person=person only %}\n\n'
            "Invoice {{ invoice.number }} for {{ invoice.total|floatformat:2 }} is ready.\n\n"
            '{% include "blocks/button" with label="View the invoice" url=invoice.url only %}\n\n'
            '{% include "parts/signature" only %}'
        ),
        "variables": {
            "person.first_name": "The recipient's first name; may be empty.",
            "invoice.number": "The invoice number.",
            "invoice.total": "The amount due.",
            "invoice.due": "When it is due.",
            "invoice.url": "Where the invoice is read.",
        },
        "sample": {
            "person": {"first_name": "Ana"},
            "invoice": {"number": "INV-0042", "total": "120.00", "due": "2026-12-01",
                        "url": "https://app.example.test/invoices/inv_42"},
        },
    },
}

_NOTICE: dict[str, Any] = {
    "name": "Notice",
    "description": "A short boxed note, for news a mail must not bury.",
    "format": "html",
    "source": (
        '<p class="brand-text" style="margin:0 0 16px;padding:12px 16px;border:1px solid {{ brand.light.border }};'
        'color:{{ brand.light.text }}">{{ text }}</p>'
    ),
    "text_source": "{{ text }}",
    "variables": {"text": "What the note says."},
    "sample": {"text": "Our offices close on Friday."},
}

_WELCOME_AR: dict[str, Any] = {
    "name": "Welcome (Arabic)",
    "subject": "مرحبا بك في {{ brand.name }}",
    "preheader": "حسابك جاهز.",
    "source": (
        "حسابك في {{ brand.name }} جاهز. سجّل الدخول لإكمال إعداده.\n\n"
        '{% include "blocks/button" with label="تسجيل الدخول" url=sign_in_url only %}'
    ),
    "variables": {"sign_in_url": "Where the person signs in."},
    "sample": {"sign_in_url": "https://app.example.test/sign-in"},
}

_REMINDER: dict[str, Any] = {
    "name": "Reminder",
    "description": "Sent the day before a booking; its button only where there is a page to open.",
    "subject": "A reminder from {{ brand.name }}",
    "preheader": "Your booking is tomorrow.",
    "format": "html",
    "source": (
        "<p>Hi {{ person.first_name }},</p>\n<p>Your booking is tomorrow.</p>\n"
        '{% if booking_url %}{% include "blocks/button" with label="See the booking" url=booking_url only %}'
        "{% endif %}"
    ),
    "text_source": (
        "Hi {{ person.first_name }},\n\nYour booking is tomorrow.\n\n"
        '{% if booking_url %}{% include "blocks/button" with label="See the booking" url=booking_url only %}'
        "{% endif %}"
    ),
    "variables": {
        "person.first_name": "The recipient's first name; may be empty.",
        "booking_url": "Where the booking is read; may be empty, and then the mail has no button.",
    },
    # No booking_url: the include inside the condition does not make it required.
    "sample": {"person": {"first_name": "Ana"}},
}

OPENED = {
    "welcome": ("template", "example.welcome", "en", True),
    "welcomeArabic": ("template", "example.welcome", "ar", False),
    "passwordReset": ("template", "example.password_reset", "en", False),
    "invoiceSent": ("template", "example.invoice_sent", "en", False),
    "button": ("block", "button", "en", True),
    "quote": ("block", "quote", "en", False),
    "recordCard": ("block", "record_card", "en", False),
    "notice": ("block", "notice", "en", False),
    "conditionalInclude": ("template", "example.reminder", "en", False),
}
"""The rows the stories and native tests open, by the name they use, as (kind, key, language, shipped)."""

_INSERTS = (
    # A new key with no row anywhere: it starts as its kind's starting row.
    {"kind": "template", "key": "example.announcement", "language": "en", "name": "Announcement"},
    # Refused: a shipped identity, one the deployment has, a key its validator refuses.
    {"kind": "layout", "key": "base", "language": "en", "name": "Base"},
    {"kind": "block", "key": "notice", "language": "en", "name": "Notice"},
    {"kind": "block", "key": "Not A Key", "language": "en", "name": "Bad key"},
    # Refused before any field: a new language seeded from a row whose includes nest past the depth.
    {"kind": "part", "key": "nested", "language": "de", "name": "Nested"},
)

_ROW = (
    "id display_name kind key language name description format subject preheader source text_source layout "
    "variables sample is_baseline created_at updated_at permissions carries_message_fields is_effective "
    "is_overridden overrides_default has_updated_default can_override can_keep_override redacted_fields"
)
"""Every field a row's list or record read selects; the fixture serves one row shape to both."""
_KIT = "id display_name name is_default"
_PREVIEW = (
    "query($template: ID!, $brand_kit: ID, $patch: message_templates_set_input) { message_template_preview("
    "template: $template, brand_kit: $brand_kit, patch: $patch) { rendered { subject preheader html plain_text "
    "language direction } warnings { code detail field } } }"
)
_BACKLINKS = (
    "query($id: String!) { message_templates_by_pk(id: $id) { backlinks { id display_name kind key language "
    "is_overridden } } }"
)
_DELETE = (
    "mutation($id: ID!, $confirm: Boolean) { message_templates_delete(id: $id, confirm: $confirm) { "
    "total_deleted_count deleted { label count } updated { label count } blocked { label count } has_blockers "
    "refusals root { label object_label object_id children { label object_label object_id } } } }"
)
_ACTION = "{ ok message validation_errors id }"
_KEEP = f"mutation($id: ID!) {{ keep_message_template_override(template: $id) {_ACTION} }}"
_INSERT = (
    "mutation($object: message_templates_insert_input!, $idempotency_key: String) "
    f"{{ insert_message_templates_one(object: $object, idempotency_key: $idempotency_key) {{ {_ROW} }} }}"
)
_UPDATE = (
    "mutation($id: String!, $set: message_templates_set_input!) "
    f"{{ update_message_templates_by_pk(pk_columns: {{id: $id}}, _set: $set) {{ {_ROW} }} }}"
)
_BY_PK = f"query($id: String!) {{ message_templates_by_pk(id: $id) {{ {_ROW} }} }}"
_SAVED_AT = "2026-11-24T09:00:00+00:00"
_REFUSED_SET = {"format": "html", "layout": ""}
"""An edit Save refuses for two reasons at once: a template with no layout, in HTML with no plain-text form."""
_UNSAVED = (
    {"subject": "Your new {{ brand.name }} password"},
    {"sample": {"person": {"first_name": "Ana"}}},
    _REFUSED_SET,
)
"""The edits previewed unsaved on the password reset: a subject that renders, a sample without the reset link, which
saves but does not render (``RENDER_ERROR``), and the refused save's own (``SAVE_REFUSED``)."""


def _alias(kind: str, key: str, language: str, shipped: bool) -> str:
    """A row's stable public id in the fixture: its kind, key, language and whether it is shipped."""

    return f"mtl_{kind.lower()}_{key.replace('.', '_')}_{language}{'_shipped' if shipped else ''}"


def _row_alias(row: Any) -> str:
    return _alias(str(row.kind), row.key, row.language, row.is_baseline)


@contextmanager
def _rolled_back() -> Iterator[None]:
    """Run one recording against the library as it stands, then put it back."""

    with transaction.atomic():
        yield
        transaction.set_rollback(True)


def _system() -> Any:
    return system_context(reason="tests.messaging_templates.console_fixture")


def _save(model: Any, **values: Any) -> Any:
    row = model(**values)
    row.full_clean()
    row.save()
    return row


def _library(model: Any, kit_model: Any) -> None:
    """The shipped library and kit, three shipped templates standing in for another addon's, two more shipped
    layouts, the deployment's overrides (one current and one whose default changed, per kind), its own ``notice``
    block and its own Arabic ``example.welcome``, and a second kit."""

    Resource.objects.load_addons((apps.get_app_config("brandkit"),), tiers=[Resource.Tier.INSTALL])
    Resource.objects.load_addons((apps.get_app_config("messaging_templates"),), tiers=[Resource.Tier.INSTALL])
    with _system():
        _save(kit_model, name="Sandstone", brand="#b45309", accent="#0d9488", neutral="#78716c", canvas="#fafaf9",
              surface="#ffffff", rail="#292524", success="#16a34a", warning="#d97706", danger="#dc2626",
              info="#2563eb", font_heading="Georgia, 'Times New Roman', serif", font_body="system-ui, sans-serif",
              footer_organization="Sandstone", footer_address="1 Example Street, 12345 Example City")
        for key, values in _SHIPPED_LAYOUTS.items():
            _save(model, kind="layout", key=key, language="en", format="html", is_baseline=True,
                  source=_LAYOUT["source"], text_source=_LAYOUT["text_source"], **values)
        for key, values in _TEMPLATES.items():
            _save(model, kind="template", key=key, language="en", format="markdown", layout="base", is_baseline=True,
                  **values)
        _save(model, kind="block", key="notice", language="en", **_NOTICE)
        _save(model, kind="template", key="example.welcome", language="ar", format="markdown", layout="base",
              description="", **_WELCOME_AR)
        _save(model, kind="template", key="example.reminder", language="en", layout="base", **_REMINDER)
        # Rows stored before the include depth was limited, written past clean() as such rows were: a part whose
        # includes nest one level past the depth, which a new language of its key is seeded from.
        for index in (3, 2, 1, 0):
            source = f'{{% include "parts/chain{index + 1}" only %}}' if index < 3 else "The end of the chain."
            model.objects.create(kind="part", key=f"chain{index}", language="en", name=f"Chain {index}",
                                 format="markdown", source=source)
        model.objects.create(kind="part", key="nested", language="en", name="Nested", format="markdown",
                             source='{% include "parts/chain0" only %}')
        shipped = {(row.kind, row.key): row for row in model.objects.filter(is_baseline=True, language="en")}

        def override(kind: str, key: str, **changes: Any) -> None:
            row = model.objects.override(shipped[(kind, key)])
            for name, value in changes.items():
                setattr(row, name, value)
            row.full_clean()
            row.save()

        def change_default(kind: str, key: str, **changes: Any) -> None:
            row = shipped[(kind, key)]
            for name, value in changes.items():
                setattr(row, name, value)
            row.full_clean()
            row.save()

        override("layout", "letter", name="Letter (ours)",
                 source=_LAYOUT["source"].replace("max-width:600px", "max-width:640px"))
        override(
            "template", "example.password_reset",
            subject="Reset your {{ brand.name }} password",
            preheader="",
            source=(
                '{% include "parts/greeting" with person=person only %}\n\n'
                "Someone asked to reset the password of your {{ brand.name }} account.\n\n"
                '{% include "blocks/notice" with text="The link works for one hour." only %}\n\n'
                '{% include "blocks/button" with label="Choose a new password" url=reset_url only %}\n\n'
                '{% include "parts/signature" only %}'
            ),
            variables={**_TEMPLATES["example.password_reset"]["variables"], "support_url": "Where to ask for help."},
            sample={**_TEMPLATES["example.password_reset"]["sample"], "support_url": "https://app.example.test/help"},
        )
        override("block", "quote", description="Someone's words, set apart on a thicker rule.",
                 source=shipped[("block", "quote")].source.replace("border-left:3px", "border-left:4px"))
        override("part", "signature", source="Warm regards,\nthe {{ brand.name }} team")
        override("layout", "compact")
        change_default("layout", "compact", source=_LAYOUT["source"].replace("max-width:600px", "max-width:480px"))
        # The deployment's sample lost the values the template reads, so its preview no longer renders.
        override("template", "example.invoice_sent",
                 sample={"person": {"first_name": "Ana"}, "invoice": {"number": "INV-0042"}})
        change_default("template", "example.invoice_sent",
                       preheader='Due {{ invoice.due|date:"DATE_FORMAT" }}; pay by card or transfer.')
        override("block", "record_card")
        change_default("block", "record_card",
                       source=shipped[("block", "record_card")].source.replace("padding:14px 16px", "padding:16px"))
        override("part", "ignore_notice")
        change_default("part", "ignore_notice", source="If you did not expect this email, you can safely ignore it.")


def _aliased(value: Any, ids: dict[str, str]) -> Any:
    """``value`` with every public id it holds, as a key, a value or a part of a value, replaced by its alias.

    One substitution over all the ids at once, the longest first in the alternation, so an id that is a
    prefix of another never matches inside it and nothing is substituted inside an alias already written.
    """

    if not ids:
        return value
    pattern = re.compile("|".join(re.escape(id_) for id_ in sorted(ids, key=len, reverse=True)))

    def alias(text: str) -> str:
        return pattern.sub(lambda match: ids[match.group(0)], text)

    def walk(item: Any) -> Any:
        if isinstance(item, dict):
            return {alias(key) if isinstance(key, str) else key: walk(inner) for key, inner in item.items()}
        if isinstance(item, list):
            return [walk(inner) for inner in item]
        return alias(item) if isinstance(item, str) else item

    return walk(value)


def _settled(row: dict[str, Any]) -> dict[str, Any]:
    return {**row, "created_at": _SAVED_AT, "updated_at": _SAVED_AT}


def _patch(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """What a verb changed on a row that existed before it: its id and each field that differs, timestamps aside."""

    return {"id": after["id"], **{
        name: value for name, value in after.items()
        if name not in {"created_at", "updated_at"} and before.get(name) != value
    }}


def _error(result: Any) -> dict[str, Any]:
    assert result.errors, result
    error = result.errors[0]
    return {"message": error.message, "path": list(error.path or []), "extensions": error.extensions or {}}


def _record() -> dict[str, Any]:  # noqa: PLR0915 -- one recording, read top to bottom
    model = apps.get_model("messaging_templates", "MessageTemplate")
    kit_model = apps.get_model("brandkit", "BrandKit")
    _library(model, kit_model)
    admin = create_platform_admin("messaging-templates-fixture-admin")
    member = create_user("messaging-templates-fixture-member")
    schemas = GraphQLSchemas(enabled_apps(("angee.messaging_templates",), group="angee.addons").app_configs)
    schema = schemas.build("console")
    with _system():
        ids = {public_id_of(row): _row_alias(row) for row in model.objects.all()}
        ids.update({public_id_of(kit): f"bkt_{kit.name.lower()}" for kit in kit_model.objects.all()})

    def read(document: str, variables: dict[str, Any], root: str, user: Any = admin) -> Any:
        return result_data(execute_schema(schema, document, variables, user=user))[root]

    def new_ids() -> dict[str, str]:
        """The aliases of rows a rolled-back recording created: the next one reuses their database ids."""

        with _system():
            return {public_id_of(row): _row_alias(row) for row in model.objects.all() if public_id_of(row) not in ids}

    console: Any = schemas.render_metadata()["console"]
    resources = {
        resource["modelLabel"]: resource
        for resource in console["angee"]["resources"]
        if resource["modelLabel"] in {MODEL, KIT_MODEL}
    }
    list_document = f"{{ message_templates {{ {_ROW} }} }}"
    rows = read(list_document, {}, "message_templates")
    initial = {row["id"]: row for row in rows}
    by_alias = {ids[row["id"]]: row["id"] for row in rows}
    opened = {name: by_alias[_alias(*identity)] for name, identity in OPENED.items()}
    member_view = {}
    for own, theirs in zip(rows, read(list_document, {}, "message_templates", member), strict=True):
        assert own["id"] == theirs["id"]
        member_view[own["id"]] = {name: value for name, value in theirs.items() if own[name] != value}
    kits = read(f"{{ brand_kits {{ {_KIT} }} }}", {}, "brand_kits")
    other_kit = next(kit["id"] for kit in kits if not kit["is_default"])

    def preview(id_: str, kit: str | None = None, patch: dict[str, Any] | None = None) -> Any:
        variables = {"template": id_, **({"brand_kit": kit} if kit else {}), **({"patch": patch} if patch else {})}
        return read(_PREVIEW, variables, "message_template_preview")

    def backlinks(id_: str) -> Any:
        return read(_BACKLINKS, {"id": id_}, "message_templates_by_pk")["backlinks"]

    def changed() -> dict[str, Any]:
        """What a verb changed in the library, read in its own recording: new rows whole and with their preview and
        backlinks, changed rows as patches, deleted ids."""

        now = {row["id"]: row for row in read(list_document, {}, "message_templates")}
        new = [now[id_] for id_ in now if id_ not in initial]
        return {
            "rows": [
                *(_settled(row) for row in new),
                *(patch for id_ in initial if id_ in now and len(patch := _patch(initial[id_], now[id_])) > 1),
            ],
            "removed": [id_ for id_ in initial if id_ not in now],
            "previews": {row["id"]: preview(row["id"]) for row in new},
            "backlinks": {row["id"]: backlinks(row["id"]) for row in new},
        }

    deletes = {}
    for id_ in opened.values():
        if "delete" not in initial[id_]["permissions"]:
            continue
        with _rolled_back():
            entry: dict[str, Any] = {
                "preview": read(_DELETE, {"id": id_, "confirm": False}, "message_templates_delete"),
            }
            if not entry["preview"]["has_blockers"]:
                read(_DELETE, {"id": id_, "confirm": True}, "message_templates_delete")
                entry["changes"] = changed()
            deletes[id_] = entry

    overrides = {}
    with _rolled_back():
        result = read(f"mutation($id: ID!) {{ override_message_template(template: $id) {_ACTION} }}",
                      {"id": opened["welcome"]}, "override_message_template")
        overrides[opened["welcome"]] = _aliased({"result": result, "changes": changed()}, new_ids())

    keeps = {}
    with _rolled_back():
        result = read(_KEEP, {"id": opened["recordCard"]}, "keep_message_template_override")
        keeps[opened["recordCard"]] = {"result": result, "changes": changed()}

    # Keep override on an override another session deleted since the page showed it: refused, in the server's words.
    keep_refusals = {}
    with _rolled_back():
        with _system():
            next(row for row in model.objects.all() if public_id_of(row) == opened["recordCard"]).delete()
        keep_refusals[opened["recordCard"]] = read(
            _KEEP, {"id": opened["recordCard"]}, "keep_message_template_override",
        )

    inserts = []
    for object_ in _INSERTS:
        with _rolled_back():
            inserted = execute_schema(schema, _INSERT, {"object": object_, "idempotency_key": "fixture"}, user=admin)
            if inserted.errors:
                inserts.append({"object": object_, "error": _error(inserted)})
                continue
            state = changed()
            # The new row, first among the changes, is what the insert answered.
            assert state["rows"][0] == _settled(inserted.data["insert_message_templates_one"])
            inserts.append(_aliased({"object": object_, "changes": state}, new_ids()))

    # Saves the server refuses, by what they show: a template in HTML with no plain-text form and no layout (the edit
    # the refused unsaved preview records too); a declared path over its 200 characters; a description over its 1,000.
    quote_variables = initial[opened["quote"]]["variables"]
    saves = {}
    for why, id_, set_ in (
        ("layout", opened["passwordReset"], _REFUSED_SET),
        ("variablePath", opened["quote"], {"variables": {**quote_variables, "a" * 201: "A value nothing reads."}}),
        ("variableDescription", opened["quote"], {"variables": {**quote_variables, "author": "x" * 1001}}),
    ):
        with _rolled_back():
            refused = execute_schema(schema, _UPDATE, {"id": id_, "set": set_}, user=admin)
            saves[why] = {"id": id_, "set": set_, "error": _error(refused)}

    # Saves the server keeps, each with what a reload then reads: a source pasted with carriage returns on a current
    # override, and a description on an override whose shipped default changed.
    saved = []
    for name, set_ in (
        ("passwordReset", {"source": initial[opened["passwordReset"]]["source"].replace("\n", "\r\n")}),
        ("recordCard", {"description": "Kept as it is until the new card is read."}),
    ):
        with _rolled_back():
            answer = read(_UPDATE, {"id": opened[name], "set": set_}, "update_message_templates_by_pk")
            reread = read(_BY_PK, {"id": opened[name]}, "message_templates_by_pk")
            saved.append({"id": opened[name], "set": set_, "row": _settled(answer), "reread": _settled(reread)})
    pasted, kept = saved
    assert "\r" not in pasted["row"]["source"] and "\r" not in pasted["reread"]["source"], pasted
    assert pasted["row"]["has_updated_default"] is pasted["reread"]["has_updated_default"] is False, pasted
    assert kept["row"]["has_updated_default"] is kept["reread"]["has_updated_default"] is True, kept

    assert set(initial) == {row["id"] for row in read(list_document, {}, "message_templates")}
    return _aliased({
        "metadata": resources,
        "opened": opened,
        "rows": [_settled(row) for row in rows],
        "member": member_view,
        "kits": kits,
        "backlinks": {id_: backlinks(id_) for id_ in opened.values()},
        "previews": {id_: preview(id_) for id_ in opened.values()},
        "previewsInKit": {other_kit: {opened["welcome"]: preview(opened["welcome"], other_kit)}},
        "delete": deletes,
        "override": overrides,
        "keep": keeps,
        "keepRefused": keep_refusals,
        "insert": inserts,
        "save": saves,
        "saved": saved,
        "unsavedPreviews": [
            {"id": opened["passwordReset"], "patch": patch, "result": preview(opened["passwordReset"], patch=patch)}
            for patch in _UNSAVED
        ],
    }, ids)


def test_the_console_fixture_is_what_the_server_says(db: None) -> None:
    """The committed fixture equals a fresh recording; ``ANGEE_RECORD_FIXTURES=1`` writes the recording instead."""

    del db
    text = json.dumps(_record(), indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    if RECORD:
        FIXTURE.write_text(text, encoding="utf-8")
    committed = json.loads(FIXTURE.read_text(encoding="utf-8"))
    recorded = json.loads(text)
    drifted = sorted(key for key in committed.keys() | recorded.keys() if committed.get(key) != recorded.get(key))
    assert not drifted, (
        f"{FIXTURE.name} no longer says what the server says ({', '.join(drifted)}): re-record it with "
        "ANGEE_RECORD_FIXTURES=1 (docs/checks.md) and review its diff."
    )
    assert FIXTURE.read_text(encoding="utf-8") == text, f"{FIXTURE.name} is not written as the recorder writes it."
