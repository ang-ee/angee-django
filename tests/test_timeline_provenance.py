"""A closed changeset projects one `provenance.change` event per record it touched."""

from __future__ import annotations

import pytest
from django.apps import apps
from django.test import override_settings

from angee.changesets import changeset as open_changeset
from angee.identity import instance_from_public_id, public_id_of
from angee.timeline.events import sync_events
from tests.conftest import create_platform_admin, vault_for

KINDS = ["angee.provenance.timeline.timeline_kinds"]


@pytest.mark.django_db(transaction=True)
@override_settings(ANGEE_TIMELINE_KINDS=KINDS)
def test_closing_a_changeset_projects_its_records(composed_permissions: None) -> None:
    owner = create_platform_admin("owner")
    # A boundary of its own closes on exit; the close is what projects the events.
    with open_changeset(fresh=True, change_reason="vault"):
        vault = vault_for(owner)
    event_model = apps.get_model("timeline", "Event")
    events = list(event_model.objects.for_record(vault).filter(kind="provenance.change").order_by("at", "pk"))
    assert events, "the vault's creating changeset closed without projecting an event"
    event = events[-1]
    changeset_model = apps.get_model("provenance", "Changeset")
    changeset = instance_from_public_id(changeset_model, event.target_id, queryset=changeset_model._base_manager.all())
    assert changeset is not None
    assert event.key == f"provenance.change:{public_id_of(changeset)}:knowledge.Vault/{vault.pk}"
    assert event.target_model == "provenance.Changeset"
    assert event.title.endswith(changeset.get_activity_display())
    assert event.at == changeset.occurred_at
    assert event.parent_id is None
    # Projecting the same changeset again changes nothing.
    assert sync_events(changeset) == 0
