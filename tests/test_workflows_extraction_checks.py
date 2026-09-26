"""Extraction settings fail at Django's system-check boundary."""

import pytest
from django.core import checks
from django.core.checks.registry import registry
from django.test import override_settings

from angee.workflows_extraction.checks import check_extraction_settings


@pytest.mark.parametrize("engines", [None, {}, {"custom": "retired.ExtractionEngine"}])
def test_extraction_check_rejects_retired_engine_setting(engines: object) -> None:
    assert check_extraction_settings in registry.get_checks()

    with override_settings(ANGEE_EXTRACTION_ENGINE_CLASSES=engines):
        [error] = check_extraction_settings()

    assert error.id == "angee.workflows_extraction.E001"
    assert error.level == checks.ERROR
    assert "ANGEE_EXTRACTION_ENGINE_CLASSES" in error.msg
    assert error.hint is not None
    assert "ANGEE_EXTRACTION_PROFILE_CLASSES" in error.hint


def test_extraction_check_accepts_profile_setting() -> None:
    with override_settings(ANGEE_EXTRACTION_PROFILE_CLASSES={}):
        assert check_extraction_settings() == []
