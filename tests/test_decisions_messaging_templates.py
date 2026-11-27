"""The decision mail's shipped rows: they load as baselines over the library, and every sample renders."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from django.apps import apps
from rebac import system_context

from angee.brandkit.testing.models import BrandKit
from angee.decisions_messaging.models import (
    DIGEST_LINKS,
    DIGEST_TEMPLATE_KEY,
    QUESTION_LINK,
    DecisionNoticeOccasion,
    mail_reason,
)
from angee.decisions_messaging.testing.drivers import MAIL_ADDONS, install_decision_mail, load_mail_templates
from angee.messaging_templates.testing.models import MessageTemplate
from angee.resources.testing.models import Resource
from tests.test_messaging_compose import MAILERS

ROOT = Path(__file__).resolve().parents[1]
SHIPPED = ROOT / "src/angee/decisions_messaging/resources/install/010_messaging_templates.messagetemplate.yaml"


@pytest.fixture
def library(db: None) -> dict[str, Any]:
    del db
    load_mail_templates()
    with system_context(reason="tests.decisions_messaging.templates"):
        return {
            "kit": BrandKit.objects.default(),
            "rows": {
                (row.kind, row.key, row.language): row
                for row in MessageTemplate.objects.filter(key__startswith="decisions_messaging.")
            },
        }


def test_the_shipped_rows_load_as_baselines_and_a_reload_changes_nothing(library: Any) -> None:
    shipped = {(row["kind"], row["key"], row["language"]) for row in yaml.safe_load(SHIPPED.read_text())}
    assert set(library["rows"]) == shipped
    assert all(row.is_baseline for row in library["rows"].values())
    keys = {occasion.template_key for occasion in DecisionNoticeOccasion} | {DIGEST_TEMPLATE_KEY}
    assert {("template", key, "en") for key in keys} <= shipped
    result = Resource.objects.load_addons(
        tuple(apps.get_app_config(label) for label in MAIL_ADDONS), tiers=[Resource.Tier.INSTALL],
    )
    assert (result.created, result.updated) == (0, 0)


def test_every_shipped_row_renders_its_sample_with_no_warning(library: Any) -> None:
    with system_context(reason="tests.decisions_messaging.templates"):
        for row in library["rows"].values():
            preview = row.preview(kit=library["kit"])
            assert preview.rendered is not None, (row.key, preview.warnings)
            assert preview.warnings == [], (row.key, preview.warnings)
            for form in (preview.rendered.html, preview.rendered.plain_text):
                assert not re.search(r"\{\{|\{%|\{#", form), (row.key, form)
            sample = row.sample.get("question") or row.sample.get("questions", [{}])[0]
            for text in (sample["url"], *sample["choices"]):
                assert text in preview.rendered.plain_text, (row.key, text)


@pytest.mark.parametrize(
    ("key", "must_print"),
    [
        pytest.param(DecisionNoticeOccasion.ASKED.template_key, QUESTION_LINK, id="asked"),
        pytest.param(DecisionNoticeOccasion.REMINDER.template_key, QUESTION_LINK, id="reminder"),
        pytest.param(DIGEST_TEMPLATE_KEY, DIGEST_LINKS, id="digest"),
    ],
)
def test_each_shipped_occasion_sends_its_sample_through_the_system_send_in_the_default_kit(
    library: Any, settings: Any, key: str, must_print: tuple[str, ...],
) -> None:
    """The rows the resource loader installed, sent as the bridge sends them: named work, ``must_print``, no kit."""

    settings.MAILERS = MAILERS
    settings.DEFAULT_FROM_EMAIL = "Angee <noreply@example.test>"
    install_decision_mail()
    assert library["kit"] is not None and library["kit"].is_default
    templates = MessageTemplate.objects
    with system_context(reason=mail_reason(key)):
        row = templates.effective("template", key)
    message = templates.send_as_system(
        row, reason=mail_reason(key), to="reader@example.test", values=row.sample, must_print=must_print,
    )
    assert message.pk is not None
