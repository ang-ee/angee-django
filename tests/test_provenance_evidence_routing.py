"""Evidence resolution keeps an explicitly selected database and every reader gate."""

from types import SimpleNamespace

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from rebac import SubjectRef

from angee.base.querysets import AngeeQuerySet
from angee.provenance import evidence


@pytest.mark.parametrize("reader", ["references", "records", "admission"])
def test_evidence_readers_preserve_selected_database(monkeypatch, reader):
    reference = evidence.EvidenceReference(model="contenttypes.ContentType", id="record")
    selected = SimpleNamespace(name="selected database")
    wrong = SimpleNamespace(name="wrong default database")
    rows = AngeeQuerySet(model=ContentType)
    monkeypatch.setattr(evidence, "require_rebac_queryset", lambda model: rows)
    monkeypatch.setattr(evidence, "model_resource_type", lambda model: "evidence/test")
    observed = []

    def resolve(model, ids, *, queryset):
        observed.append(queryset.db)
        return {"record": selected if queryset.db == "evidence_secondary" else wrong}

    monkeypatch.setattr(evidence, "instances_from_public_ids", resolve)
    actor = SubjectRef.of("auth/user", "1")
    if reader == "references":
        assert evidence.readable_evidence([reference], actor, using="evidence_secondary") == [reference]
    elif reader == "records":
        records = evidence.readable_evidence_records([reference], actor, using="evidence_secondary")
        assert records == [(reference, selected)]
    else:
        assert evidence.readable_records([reference], [actor, actor], using="evidence_secondary") == [selected]
    assert observed and set(observed) == {"evidence_secondary"}


def test_selected_database_does_not_waive_an_unreadable_participant(monkeypatch):
    reference = evidence.EvidenceReference(model="contenttypes.ContentType", id="record")
    rows = AngeeQuerySet(model=ContentType)
    monkeypatch.setattr(evidence, "require_rebac_queryset", lambda model: rows)
    monkeypatch.setattr(evidence, "model_resource_type", lambda model: "evidence/test")
    calls = []

    def resolve(model, ids, *, queryset):
        calls.append(queryset.db)
        return {"record": object()} if len(calls) == 1 else {}

    monkeypatch.setattr(evidence, "instances_from_public_ids", resolve)
    with pytest.raises(PermissionDenied, match="Every participant"):
        evidence.readable_records([reference], [SubjectRef.of("auth/user", "1"), SubjectRef.of("auth/user", "2")], using="evidence_secondary")
    assert calls == ["evidence_secondary", "evidence_secondary"]
