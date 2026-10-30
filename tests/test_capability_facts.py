"""Package documents share one capability-table projection."""

from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured

from angee.base.addons import capability_facts


def test_source_documents_preserve_capability_namespaces() -> None:
    document = {"tool": {"angee": {"platform": {"category": "system"}}, "other": {"api": {"enabled": True}}}}
    assert capability_facts(document, "angee.platform") == {"category": "system"}
    assert capability_facts(document, "other.api") == {"enabled": True}
    assert capability_facts(document, "angee.resources") == {}
    assert capability_facts({}, "angee.platform") == {}


@pytest.mark.parametrize("document", [{"tool": []}, {"tool": {"angee": []}}, {"tool": {"angee": {"platform": []}}}])
def test_non_table_capability_facts_are_rejected(document: dict[str, object]) -> None:
    with pytest.raises(ImproperlyConfigured, match=r"tool\.angee\.platform must be a TOML table"):
        capability_facts(document, "angee.platform")
