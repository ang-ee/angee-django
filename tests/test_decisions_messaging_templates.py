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
from angee.decisions_messaging.models import DIGEST_TEMPLATE_KEY, DecisionNoticeOccasion
from angee.decisions_messaging.testing.drivers import MAIL_ADDONS, load_mail_templates
from angee.messaging_templates.testing.models import MessageTemplate
from angee.resources.testing.models import Resource

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
