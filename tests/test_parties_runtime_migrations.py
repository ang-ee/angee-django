"""Rehearse the parties statement cutover chain on legacy carriers.

The host's composed tables carry the new schema; the fixture adds the retired
legacy shapes (PartyHandle, MergeVeto, CardDAV card columns, relationship and roster
source labels) to the project state and the database, seeds the row shapes a live
database holds, and replays the declared migrations against that historical state.
"""

from __future__ import annotations

import datetime
import tomllib
from pathlib import Path
from typing import Any

import pytest
from django.apps import apps
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.management.sql import emit_post_migrate_signal
from django.db import connection, migrations, models
from django.db.migrations.state import ModelState, ProjectState
from django.utils import timezone
from rebac import SubjectRef, app_settings, system_context
from rebac.actors import to_subject_ref

from angee.base.changesets import changeset
from angee.base.fields import StateField
from angee.base.managers import StatementManager
from angee.base.mixins import SourceKind
from angee.integrate.runtime_migrations import (
    fold_record_sync_history,
    record_link_failure_status,
    record_link_history_state,
    record_sync_state,
    retire_record_sync_hash_bases,
    retire_record_sync_stores,
)
from angee.messaging.testing.models import Directory, Party
from angee.parties.managers import statement_fold_reasons
from angee.parties.runtime_migrations import (
    circle_member_decisions,
    fold_party_aspects,
    fold_party_handle_statements,
    party_aspect_schema,
    party_statement_schema,
    preflight_party_folds,
    retire_party_carriers,
    retire_party_handle,
)
from angee.provenance.runtime_migrations import changeset_schema, retire_reversion, statement_principal_schema
from angee.spaces.runtime_migrations import membership_decisions
from tests.conftest import create_user, make_integration

CARD_COLUMNS = ("folder", "source_uid", "source_etag", "raw_vcard", "extensions")
VCARD = "\r\n".join(
    (
        "BEGIN:VCARD",
        "VERSION:3.0",
        "FN:Olek Example",
        "N:;Olek",
        "REV:2024-12-01T22:35:13Z",
        "UID:card-1",
        "END:VCARD",
        "",
    )
)
VCARD_REORDERED = "\r\n".join(
    (
        "BEGIN:VCARD",
        "VERSION:3.0",
        "UID:card-1",
        "FN:Olek Example",
        "N:;Olek",
        "REV:2025-01-01T00:00:00Z",
        "END:VCARD",
        "",
    )
)


def _card_fields(*, constrained: bool) -> dict[str, models.Field]:
    return {
        "folder": models.ForeignKey(
            "parties.Folder",
            null=True,
            blank=True,
            related_name="+",
            on_delete=models.SET_NULL if constrained else models.DO_NOTHING,
            db_constraint=constrained,
        ),
        "source_uid": models.CharField(max_length=512, blank=True, default=""),
        "source_etag": models.CharField(max_length=512, blank=True, default=""),
        "raw_vcard": models.TextField(blank=True, default=""),
        "extensions": models.JSONField(blank=True, default=dict),
    }


def _audit_fields() -> dict[str, models.Field]:
    return {
        "created_at": models.DateTimeField(default=timezone.now),
        "updated_at": models.DateTimeField(default=timezone.now),
        "created_by": models.ForeignKey(
            settings.AUTH_USER_MODEL, null=True, blank=True, related_name="+", on_delete=models.SET_NULL
        ),
        "updated_by": models.ForeignKey(
            settings.AUTH_USER_MODEL, null=True, blank=True, related_name="+", on_delete=models.SET_NULL
        ),
    }


def legacy_state() -> ProjectState:
    """The host's composed schema plus every retired legacy shape the chain reads."""
    state = ProjectState.from_apps(apps)
    party = state.models["parties", "party"]
    party.fields.update(_card_fields(constrained=True))
    party.options["constraints"] = [
        *party.options.get("constraints", []),
        models.UniqueConstraint(
            fields=("folder", "source_uid"),
            name="uq_party_folder_source_uid",
            condition=models.Q(folder__isnull=False) & ~models.Q(source_uid=""),
        ),
    ]
    for name in ("historicalparty", "historicalperson", "historicalorganization"):
        historical = state.models["parties", name]
        historical.fields.update(_card_fields(constrained=False))
        historical.fields["handle_count"] = models.PositiveIntegerField(default=0)
    relationship = state.models["parties", "relationship"]
    relationship.fields["source"] = models.CharField(max_length=16, default="manual")
    relationship.options["constraints"] = [
        *relationship.options.get("constraints", []),
        models.UniqueConstraint(
            fields=("party", "other_name"),
            name="uq_relationship_carddav_employment",
            condition=models.Q(source="carddav"),
        ),
    ]
    state.models["parties", "circlemember"].fields["source"] = models.CharField(max_length=16, default="manual")
    state.add_model(
        ModelState(
            "parties",
            "PartyHandle",
            [
                ("id", models.BigAutoField(primary_key=True)),
                *_audit_fields().items(),
                ("party", models.ForeignKey("parties.Party", on_delete=models.CASCADE, related_name="+")),
                ("handle", models.ForeignKey("parties.Handle", on_delete=models.CASCADE, related_name="+")),
                ("confidence", models.FloatField(default=1.0)),
                ("source", models.CharField(max_length=16, default="manual")),
                ("is_confirmed", models.BooleanField(default=False)),
                ("is_dismissed", models.BooleanField(default=False)),
                ("metadata", models.JSONField(blank=True, default=dict)),
            ],
            options={"db_table": "test_parties_party_handle"},
        )
    )
    state.add_model(
        ModelState(
            "parties",
            "MergeVeto",
            [
                ("id", models.BigAutoField(primary_key=True)),
                *_audit_fields().items(),
                ("party_a", models.ForeignKey("parties.Party", on_delete=models.CASCADE, related_name="+")),
                ("party_b", models.ForeignKey("parties.Party", on_delete=models.CASCADE, related_name="+")),
            ],
            options={"db_table": "test_parties_merge_veto"},
        )
    )
    return state


class LegacyHost:
    """Legacy schema on the host database, with the declared migrations replayed on it."""

    def __init__(self) -> None:
        self.state = legacy_state()
        self.apps = self.state.apps
        self.added: list[tuple[str, str]] = []
        with connection.schema_editor() as editor:
            for label in ("parties.PartyHandle", "parties.MergeVeto"):
                editor.create_model(self.model(label))
            for label, names in (
                ("parties.Party", CARD_COLUMNS),
                ("parties.HistoricalParty", (*CARD_COLUMNS, "handle_count")),
                ("parties.HistoricalPerson", (*CARD_COLUMNS, "handle_count")),
                ("parties.HistoricalOrganization", (*CARD_COLUMNS, "handle_count")),
                ("parties.Relationship", ("source",)),
                ("parties.CircleMember", ("source",)),
            ):
                model = self.model(label)
                for name in names:
                    editor.add_field(model, model._meta.get_field(name))
                    self.added.append((model._meta.db_table, model._meta.get_field(name).column))
            for label, name in (
                ("parties.Party", "uq_party_folder_source_uid"),
                ("parties.Relationship", "uq_relationship_carddav_employment"),
            ):
                # SQLite's table remake already materialized the state's constraints.
                model = self.model(label)
                with connection.cursor() as cursor:
                    present = name in connection.introspection.get_constraints(cursor, model._meta.db_table)
                if not present:
                    editor.add_constraint(model, next(c for c in model._meta.constraints if c.name == name))

    def close(self) -> None:
        # Render the teardown models without the legacy constraints: SQLite remakes a
        # table from the model when a column leaves, and the constraint names that column.
        state = legacy_state()
        constraints = {}
        for label, name in (
            ("parties.Party", "uq_party_folder_source_uid"),
            ("parties.Relationship", "uq_relationship_carddav_employment"),
        ):
            options = state.models["parties", label.rsplit(".", 1)[1].lower()].options
            constraints[label] = next(c for c in options["constraints"] if c.name == name)
            options["constraints"] = [c for c in options["constraints"] if c.name != name]
        current = state.apps
        with connection.schema_editor() as editor:
            tables = set(connection.introspection.table_names())
            for label in ("parties.MergeVeto", "parties.PartyHandle"):
                model = current.get_model(label)
                if model._meta.db_table in tables:
                    editor.delete_model(model)
            for label, constraint in constraints.items():
                model = current.get_model(label)
                with connection.cursor() as cursor:
                    present = constraint.name in connection.introspection.get_constraints(cursor, model._meta.db_table)
                if present:
                    editor.remove_constraint(model, constraint)
            for table, column in self.added:
                with connection.cursor() as cursor:
                    columns = {row.name for row in connection.introspection.get_table_description(cursor, table)}
                if column not in columns:
                    continue
                model = next(m for m in current.get_models() if m._meta.db_table == table)
                editor.remove_field(model, next(f for f in model._meta.fields if f.column == column))

    def model(self, label: str) -> Any:
        return self.apps.get_model(label)

    def rows(self, label: str) -> Any:
        return self.model(label)._base_manager.order_by()

    def create(self, model_label: str, /, **values: Any) -> Any:
        return self.rows(model_label).create(**values)

    def content_type(self, model: str) -> Any:
        row, _ = self.rows("contenttypes.ContentType").get_or_create(app_label="parties", model=model)
        return row

    def apply(self, module: Any, app_label: str = "parties") -> None:
        migration = module.Migration(f"0001_{module.__name__.rsplit('.', 1)[1]}", app_label)
        with connection.schema_editor() as editor:
            self.state = migration.apply(self.state, editor)
        self.apps = self.state.apps


@pytest.fixture
def legacy(composed_tables: None) -> Any:
    del composed_tables
    host = LegacyHost()
    try:
        yield host
    finally:
        host.close()


@pytest.fixture
def seeded(legacy: LegacyHost) -> dict[str, Any]:
    """Every row shape the review found on live databases, in its pre-fold state."""
    user_model = settings.AUTH_USER_MODEL
    person = legacy.create(
        user_model,
        username="olek",
        password="!",
        kind="person",
        email="olek@example.test",
        first_name="Olek",
        last_name="Example",
    )
    service = legacy.create(
        user_model, username="bot", password="!", kind="service", email="", first_name="", last_name=""
    )
    vendor = legacy.create(
        "integrate.Vendor", slug="carddav", display_name="CardDAV", website_url="", icon="", description=""
    )
    directory = legacy.create(
        "parties.Directory", display_name="Cards", vendor=vendor, last_used_status="", last_error=""
    )
    folder = legacy.create("parties.Folder", name="Contacts", directory=directory, source_href="/books/default/")
    # The person's own takeout account: one IMPORT source per imported account, found
    # as the importer's channel whose backend key is the handle's platform (the takeout
    # bridges are not installed in this host; slack is the installed key that is also a platform).
    takeout_channel = legacy.create(
        "messaging.Channel",
        display_name="Slack export",
        vendor=vendor,
        owner=person,
        backend_class="slack",
        last_used_status="",
        last_error="",
    )
    stream = legacy.create(
        "integrate.SyncStream",
        integration_id=directory.pk,
        key="contacts",
        partition=folder.source_href,
        kind="record_replica",
        direction="pull",
    )
    party_ct = legacy.content_type("party")
    week_ago = timezone.now() - datetime.timedelta(days=7)
    payload = {
        "display_name": "Olek Example",
        "notes": "",
        "given_name": "Olek",
        "family_name": "Example",
        "addresses": [{"label": "home", "street": "Main 1", "city": "Oslo", "postal_code": "0150"}],
        "organization": "ACME",
        "title": "CTO",
        "role": "",
    }

    def enrolled(uid: str, display_name: str, **extra: Any) -> tuple[Any, Any]:
        row = legacy.create(
            "parties.Person",
            display_name=display_name,
            given_name="Olek",
            family_name="Example",
            folder=folder,
            source_uid=uid,
            source_etag="etag-1",
            raw_vcard=VCARD,
            created_by=person,
            **extra,
        )
        # auto_now_add stamps the insert; the row's history starts a week ago.
        legacy.rows("parties.Person").filter(pk=row.pk).update(created_at=week_ago, updated_at=week_ago)
        row.refresh_from_db()
        link = legacy.create(
            "integrate.RecordLink",
            stream=stream,
            external_key=uid,
            target_content_type_id=party_ct.pk,
            target_object_id=str(row.pk),
            asserted_by="",
            source_hash="",
            dependency_digest="",
            remote_version="etag-2",
            origin="",
            status="current",
            source_payload={"href": f"/books/default/{uid}.vcf", "raw_vcard": VCARD_REORDERED},
            mapped_payload=payload,
        )
        base = legacy.create(
            "integrate.HistoricalRecordLink",
            id=link.pk,
            created_at=link.created_at,
            updated_at=link.updated_at,
            external_key=uid,
            asserted_by="",
            source_hash="",
            dependency_digest="",
            remote_version="",
            origin="",
            history_date=week_ago,
            history_type="+",
            stream_id=stream.pk,
            target_content_type_id=party_ct.pk,
            target_object_id=str(row.pk),
            mapped_payload=payload,
            applied_at=week_ago,
        )
        legacy.rows("integrate.RecordLink").filter(pk=link.pk).update(applied_base_id=base.history_id)
        return row, link

    card, card_link = enrolled("card-1", "Olek Example")
    edited, _ = enrolled("card-2", "Olek Edited", updated_by=person)
    manual = legacy.create("parties.Party", display_name="Manual Party", created_by=person)
    imported = legacy.create("parties.Party", display_name="Takeout Person", created_by=person)
    organization = legacy.create("parties.Organization", display_name="ACME", domain="acme.test", created_by=person)
    own = legacy.create("parties.Person", display_name="Olek Example", user_id=person.pk, created_by=person)
    bot_party = legacy.create("parties.Person", display_name="bot", user_id=service.pk, created_by=service)
    legacy.create(
        "parties.Address", party=card, label="home", street="Main 1", city="Oslo", postal_code="0150", created_by=person
    )
    kind = legacy.create("parties.RelationshipKind", slug="employment", name="Employment")
    legacy.create(
        "parties.Relationship",
        party=card,
        kind=kind,
        other_name="ACME",
        title="CTO",
        source="carddav",
        created_by=person,
    )
    legacy.create(
        "parties.Relationship", party=imported, kind=kind, other_name="Old Employer", source="import", created_by=person
    )

    def handle(value: str, party: Any = None, *, platform: str = "email", **extra: Any) -> Any:
        return legacy.create(
            "parties.Handle",
            platform=platform,
            value=value,
            normalized_value=value,
            party=party,
            created_by=person,
            **extra,
        )

    def carrier(party: Any, handle_row: Any, source: str, **extra: Any) -> Any:
        values = dict(created_by=person, created_at=week_ago, updated_at=week_ago, confidence=1.0, metadata={})
        values.update(extra)
        return legacy.create("parties.PartyHandle", party=party, handle=handle_row, source=source, **values)

    carriers = {
        "carddav": carrier(card, handle("olek@example.test", card), "carddav"),
        "twin": carrier(manual, handle("manual@example.test", manual), "email_match"),
        "domain": carrier(organization, handle("someone@acme.test"), "rule", confidence=0.4),
        "signature": carrier(
            organization,
            handle("+4712345678"),
            "rule",
            confidence=0.3,
            metadata={"evidence": {"kind": "signature_phone", "fragment_hash": "f" * 8}},
        ),
        "oauth": carrier(own, handle("olek@login.test", own, owner=person), "oauth", is_confirmed=True),
        "service": carrier(
            bot_party,
            handle("bot@example.test", bot_party, owner=service),
            "manual",
            is_confirmed=True,
            created_by=service,
            updated_by=service,
        ),
        "takeout": carrier(
            imported, handle("takeout-123", platform="slack"), "import", metadata={"provenance": "social_takeout"}
        ),
        "takeout_orphan": carrier(
            imported, handle("takeout-456", platform="linkedin"), "import", metadata={"provenance": "social_takeout"}
        ),
    }
    legacy.create(
        "parties.MergeVeto",
        party_a_id=min(manual.pk, imported.pk),
        party_b_id=max(manual.pk, imported.pk),
        created_by=person,
    )
    return dict(
        person=person,
        service=service,
        directory=directory,
        takeout_channel=takeout_channel,
        card=card,
        card_link=card_link,
        edited=edited,
        manual=manual,
        imported=imported,
        organization=organization,
        own=own,
        bot_party=bot_party,
        carriers=carriers,
        week_ago=week_ago,
    )


def _statements(legacy: LegacyHost, party: Any, **filters: Any) -> list[Any]:
    return list(
        legacy.rows("parties.PartyStatement")
        .filter(subject_id=party.pk, superseded__isnull=True, **filters)
        .order_by("pk")
    )


def _user(user: Any) -> str:
    return str(SubjectRef.of(app_settings.REBAC_USER_TYPE, str(user.pk)))


def _rule(legacy: LegacyHost, key: str) -> str:
    return f"provenance/rule:{legacy.rows('provenance.Rule').get(key=key).pk}"


@pytest.mark.django_db(transaction=True)
def test_preflight_names_every_blocking_row_before_anything_runs(legacy: LegacyHost, seeded: dict[str, Any]) -> None:
    """Blockers of both folds come back together; previously blocking producers are not among them."""
    person, organization, manual = seeded["person"], seeded["organization"], seeded["manual"]
    unknown = legacy.create(
        "parties.PartyHandle",
        party=organization,
        source="rule",
        confidence=0.3,
        created_by=person,
        handle=legacy.create("parties.Handle", platform="email", value="u@acme.test", normalized_value="u@acme.test"),
        metadata={"evidence": {"kind": "llm_guess"}},
    )
    dangling = legacy.create(
        "parties.PartyHandle",
        party=manual,
        source="manual",
        created_by=person,
        handle=legacy.create("parties.Handle", platform="email", value="d@acme.test", normalized_value="d@acme.test"),
        metadata={"evidence_refs": [{"model": "parties.Handle", "id": "hdl_missing"}]},
    )
    veto = legacy.create(
        "parties.MergeVeto",
        party_a_id=min(manual.pk, organization.pk),
        party_b_id=max(manual.pk, organization.pk),
        created_by=seeded["service"],
    )

    rows, notices = preflight_party_folds.blockers(legacy.apps, connection.alias)

    # A veto recorded without a person is a standing decision, not an upgrade blocker (ruling).
    assert {(row.label, row.pk) for row in rows} == {
        ("PartyHandle", unknown.pk),
        ("PartyHandle", dangling.pk),
    }
    assert any("takeout import without one identified account channel (social_takeout)" in reason for reason in notices)
    assert any("anti-link without a recorded person" in reason for reason in notices)
    with pytest.raises(ValueError, match=rf"2 rows block.*\n.*{unknown.pk}.*\n.*{dangling.pk}"):
        legacy.apply(preflight_party_folds)
    assert not legacy.rows("parties.PartyStatement").exists()
    for row in (unknown, dangling):
        legacy.rows("parties.PartyHandle").filter(pk=row.pk).delete()
    assert preflight_party_folds.blockers(legacy.apps, connection.alias)[0] == []
    assert legacy.rows("parties.MergeVeto").filter(pk=veto.pk).exists()
    legacy.apply(preflight_party_folds)


@pytest.mark.django_db(transaction=True)
def test_handle_fold_attributes_every_live_producer_and_carries_stamps(
    legacy: LegacyHost, seeded: dict[str, Any]
) -> None:
    carriers, person, service = seeded["carriers"], seeded["person"], seeded["service"]
    legacy.apply(fold_party_handle_statements)

    directory = f"integrate/integration:{seeded['directory'].pk}"
    [card] = _statements(legacy, seeded["card"], aspect="handle")
    assert (card.asserted_by, card.source_kind, card.rank, card.applied, card.confidence) == (
        directory,
        "directory",
        "normal",
        True,
        1.0,
    )
    assert card.created_at == carriers["carddav"].created_at == seeded["week_ago"]
    assert card.evidence == [
        {
            "model": "parties.Party",
            "id": fold_party_handle_statements.Legacy(legacy.apps, connection.alias).public_id(
                "parties.party", seeded["card"].pk
            ),
        }
    ]
    [twin] = _statements(legacy, seeded["manual"], aspect="handle")
    assert (twin.asserted_by, twin.source_kind, twin.applied) == (
        _rule(legacy, "parties.normalized_twin"),
        "rule",
        True,
    )
    domain, signature = _statements(legacy, seeded["organization"], aspect="handle")
    assert {domain.asserted_by, signature.asserted_by} == {
        _rule(legacy, "parties.email_domain"),
        _rule(legacy, "parties.signature_phone"),
    }
    assert {domain.confidence, signature.confidence} == {0.4, 0.3} and not domain.applied and not signature.applied
    [own] = _statements(legacy, seeded["own"], aspect="handle")
    assert (own.asserted_by, own.source_kind, own.rank, own.snak, own.applied) == (
        _user(person),
        "person",
        "preferred",
        "value",
        True,
    )
    [bot] = _statements(legacy, seeded["bot_party"], aspect="handle")
    seed = legacy.rows("provenance.Seed").get(key=fold_party_handle_statements.SYSTEM_KEY)
    assert (bot.asserted_by, bot.source_kind, bot.rank, bot.snak, bot.applied) == (
        f"provenance/seed:{seed.pk}",
        "seed",
        "normal",
        "value",
        True,
    )
    assert bot.evidence == [
        {
            "model": "iam.User",
            "id": fold_party_handle_statements.Legacy(legacy.apps, connection.alias).public_id("iam.user", service.pk),
        }
    ]
    # The slack takeout folds under the importer's account channel; the linkedin one has no channel.
    (takeout,) = _statements(legacy, seeded["imported"])
    assert (takeout.asserted_by, takeout.source_kind, takeout.rank, takeout.applied) == (
        f"integrate/integration:{seeded['takeout_channel'].pk}",
        "import",
        "normal",
        True,
    )
    assert takeout.handle_id == seeded["carriers"]["takeout"].handle_id
    history = legacy.rows("parties.HistoricalPartyStatement").filter(history_change_reason="fold.party_handle")
    assert history.filter(history_type="+").count() == 7  # the six producers above plus the takeout
    changeset = legacy.rows(settings.ANGEE_CHANGESET_MODEL).get(change_reason="fold.party_handle")
    assert changeset.counts == {"parties.partystatement": history.count()}


@pytest.mark.django_db(transaction=True)
def test_aspect_fold_proves_against_the_applied_snapshot_and_credits_only_persons(
    legacy: LegacyHost, seeded: dict[str, Any]
) -> None:
    # A second keep-separate recorded by a service account: a standing decision by the fold's seed.
    legacy.create(
        "parties.MergeVeto",
        party_a_id=min(seeded["manual"].pk, seeded["organization"].pk),
        party_b_id=max(seeded["manual"].pk, seeded["organization"].pk),
        created_by=seeded["service"],
    )
    legacy.apply(fold_party_handle_statements)
    legacy.apply(fold_party_aspects)

    directory = f"integrate/integration:{seeded['directory'].pk}"
    card = {row.aspect: row for row in _statements(legacy, seeded["card"])}
    assert card["identity"].asserted_by == card["contact"].asserted_by == directory
    assert card["identity"].link_id == seeded["card_link"].pk and card["identity"].display_name == "Olek Example"
    assert card["handle"].link_id == seeded["card_link"].pk  # the folded directory claim gained its card link
    edited = {row.aspect: row for row in _statements(legacy, seeded["edited"])}
    assert (edited["identity"].asserted_by, edited["identity"].source_kind) == (_user(seeded["person"]), "person")
    assert edited["contact"].asserted_by == directory
    names = legacy.rows("parties.PersonStatement").filter(subject_id=seeded["card"].pk)
    assert {row.aspect: row.asserted_by for row in names} == {"name": directory, "dates": directory}
    [address] = legacy.rows("parties.AddressStatement").filter(subject__party_id=seeded["card"].pk)
    assert (address.asserted_by, address.street) == (directory, "Main 1")
    relationships = {
        row.subject.party_id: row for row in legacy.rows("parties.RelationshipStatement").select_related("subject")
    }
    assert relationships[seeded["card"].pk].asserted_by == directory
    assert seeded["imported"].pk not in relationships  # imported relationship: unasserted
    assert _statements(legacy, seeded["imported"], aspect="identity") == []
    [manual] = _statements(legacy, seeded["manual"], aspect="identity")
    assert manual.asserted_by == _user(seeded["person"])
    vetoes = {row.asserted_by: row for row in legacy.rows("parties.PartySameAsPairStatement")}
    seed = legacy.rows("provenance.Seed").get(key="parties.fold_party_handle")
    assert set(vetoes) == {_user(seeded["person"]), f"provenance/seed:{seed.pk}"}
    assert {(row.rank, row.snak, row.source_kind) for row in vetoes.values()} == {
        ("preferred", "no_value", "person"),
        ("preferred", "no_value", "seed"),
    }
    assert card["identity"].created_at == seeded["card"].created_at == seeded["week_ago"]


@pytest.mark.django_db(transaction=True)
def test_retirements_name_dangling_pointers_and_accept_a_reserialized_card(
    legacy: LegacyHost, seeded: dict[str, Any], capsys
) -> None:
    legacy.apply(fold_party_handle_statements)
    legacy.apply(fold_party_aspects)
    carrier_ct = legacy.content_type("partyhandle")
    veto_ct = legacy.content_type("mergeveto")
    moved = legacy.create(
        settings.ANGEE_CHANGESET_MODEL,
        activity="system",
        origin_content_type_id=carrier_ct.pk,
        origin_object_id=str(seeded["carriers"]["carddav"].pk),
    )
    stale = legacy.create(
        settings.ANGEE_CHANGESET_MODEL,
        activity="system",
        origin_content_type_id=carrier_ct.pk,
        origin_object_id="999999",
    )
    permissions = legacy.rows("auth.Permission")
    for ct, codename in ((carrier_ct, "add_partyhandle"), (veto_ct, "view_mergeveto")):
        permissions.create(content_type_id=ct.pk, codename=codename, name=codename)

    legacy.apply(circle_member_decisions)
    legacy.apply(retire_party_handle)

    out = capsys.readouterr().out
    assert f"{settings.ANGEE_CHANGESET_MODEL} {stale.pk}: points at deleted PartyHandle '999999'; left as it is" in out
    changesets = legacy.rows(settings.ANGEE_CHANGESET_MODEL)
    assert changesets.get(pk=moved.pk).origin_object_id == str(seeded["card"].pk)
    assert changesets.get(pk=moved.pk).origin_content_type_id == legacy.content_type("party").pk
    assert changesets.get(pk=stale.pk).origin_content_type_id == carrier_ct.pk
    assert not permissions.filter(codename="add_partyhandle").exists()
    assert "test_parties_party_handle" not in connection.introspection.table_names()

    legacy.apply(retire_party_carriers)

    assert not permissions.filter(codename="view_mergeveto").exists()
    with connection.cursor() as cursor:
        columns = {row.name for row in connection.introspection.get_table_description(cursor, "test_parties_party")}
    assert not columns & {"folder_id", "source_uid", "source_etag", "raw_vcard", "extensions"}
    assert "test_parties_merge_veto" not in connection.introspection.table_names()


@pytest.mark.django_db(transaction=True)
def test_carrier_retirement_reports_a_card_no_link_retains_and_drops_the_column(
    legacy: LegacyHost, seeded: dict[str, Any]
) -> None:
    legacy.apply(fold_party_handle_statements)
    legacy.apply(fold_party_aspects)
    legacy.apply(circle_member_decisions)
    legacy.apply(retire_party_handle)
    legacy.rows("parties.Party").filter(pk=seeded["card"].pk).update(raw_vcard=VCARD.replace("Olek", "Someone"))
    # A card whose raw vCard no link retains is a notice, not a refusal (ruling (b)): only its wire
    # bytes leave with the column, its values keep their folded statements, and both checks say so.
    problems, notices = retire_party_carriers.plan(legacy.apps, connection.alias, folded=False)
    assert problems == []
    assert (
        f"Party {seeded['card'].pk}: raw vCard not retained on any link; only its wire bytes leave with the column"
        in notices
    )
    legacy.apply(retire_party_carriers)
    assert "raw_vcard" not in {field.name for field in legacy.model("parties.Party")._meta.fields}


def _consumer_statement(
    legacy: LegacyHost, party: Any, aspect: str, *, history: bool = True, activity: str = "system", **values: Any
) -> Any:
    """A statement a consumer's own fold applied under its own principal and reason, with its history."""

    seed, _ = legacy.rows("provenance.Seed").get_or_create(key="tests.consumer_fold")
    changeset = legacy.create(
        settings.ANGEE_CHANGESET_MODEL, activity=activity, change_reason="fold.consumer", state="closed"
    )
    row = legacy.create(
        "parties.PartyStatement",
        subject_id=party.pk,
        aspect=aspect,
        asserted_by=f"provenance/seed:{seed.pk}",
        source_kind="seed",
        rank="normal",
        snak="value",
        status="asserted",
        applied=True,
        confidence=1.0,
        changeset_id=changeset.pk,
        **values,
    )
    if not history:
        return row
    legacy.create(
        "parties.HistoricalPartyStatement",
        **{field.attname: getattr(row, field.attname) for field in row._meta.concrete_fields},
        history_date=timezone.now(),
        history_type="+",
        history_change_reason="fold.consumer",
    )
    return row


@pytest.mark.django_db(transaction=True)
def test_a_sender_claim_written_before_the_reference_rename_folds_its_listed_references(
    legacy: LegacyHost, seeded: dict[str, Any], capsys
) -> None:
    """Until 2026-10-10 a claim kept its references under ``evidence``; over a suggestion it also kept the keys."""

    person = seeded["person"]
    ref = {
        "model": "parties.Party",
        "id": fold_party_handle_statements.Legacy(legacy.apps, connection.alias).public_id(
            "parties.party", seeded["manual"].pk
        ),
    }
    claimed = {}
    for source, listed, value in (
        ("email_match", [ref], "claim@example.test"),
        ("rule", ["kind", "source_handle", ref], "suggested@example.test"),
    ):
        handle = legacy.create(
            "parties.Handle", platform="email", value=value, normalized_value=value, created_by=person
        )
        claimed[source] = legacy.create(
            "parties.PartyHandle",
            party=seeded["organization"],
            handle=handle,
            source=source,
            confidence=0.4,
            created_by=person,
            metadata={"claim": "source_sender", "evidence": listed},
        )

    rows, notices = preflight_party_folds.blockers(legacy.apps, connection.alias)
    assert rows == []
    legacy.apply(fold_party_handle_statements)

    sender = _rule(legacy, "parties.source_sender")
    for carrier in claimed.values():
        [statement] = _statements(legacy, seeded["organization"], handle_id=carrier.handle_id)
        assert (statement.asserted_by, statement.source_kind, statement.evidence) == (sender, "rule", [ref])
    assert "1 x source_sender claim over a rule suggestion: its overwritten evidence keys dropped" in (
        capsys.readouterr().out
    )


@pytest.mark.django_db(transaction=True)
def test_a_claim_a_person_confirmed_before_sources_were_kept_folds_as_the_persons_link(
    legacy: LegacyHost, seeded: dict[str, Any], capsys
) -> None:
    """Until 2026-10-11 a confirmation rewrote the producer's source to manual and kept its claim metadata."""

    person = seeded["person"]
    ref = {
        "model": "parties.Party",
        "id": fold_party_handle_statements.Legacy(legacy.apps, connection.alias).public_id(
            "parties.party", seeded["manual"].pk
        ),
    }
    carriers = {}
    for confirmed, value in ((True, "confirmed-claim@example.test"), (False, "unconfirmed-claim@example.test")):
        handle = legacy.create(
            "parties.Handle", platform="email", value=value, normalized_value=value, created_by=person
        )
        carriers[confirmed] = legacy.create(
            "parties.PartyHandle",
            party=seeded["organization"],
            handle=handle,
            source="manual",
            confidence=1.0,
            is_confirmed=confirmed,
            created_by=person,
            updated_by=person,
            metadata={"claim": "source_sender", "evidence": [ref]},
        )

    rows, _notices = preflight_party_folds.blockers(legacy.apps, connection.alias)
    assert [(row.pk, row.reason) for row in rows] == [
        (carriers[False].pk, "manual source conflicts with retained producer evidence")
    ]
    legacy.rows("parties.PartyHandle").filter(pk=carriers[False].pk).delete()
    legacy.apply(fold_party_handle_statements)

    statements = _statements(legacy, seeded["organization"], handle_id=carriers[True].handle_id)
    # Creator and decider are one person, so the confirmation is that person's one stance on the pair.
    [statement] = statements
    assert (statement.asserted_by, statement.source_kind, statement.rank, statement.applied) == (
        _user(person),
        "person",
        "preferred",
        True,
    )
    assert statement.evidence == [ref]
    assert "1 x producer link a person confirmed before its source was kept: folded as the person's" in (
        capsys.readouterr().out
    )


@pytest.mark.django_db(transaction=True)
def test_the_folds_keep_a_statement_another_fold_already_applied(
    legacy: LegacyHost, seeded: dict[str, Any], capsys
) -> None:
    """A consumer's own fold ahead of the lane's keeps what it applied; the lane records its statements beside it."""

    twin = seeded["carriers"]["twin"]
    handle_claim = _consumer_statement(legacy, seeded["manual"], "handle", handle_id=twin.handle_id)
    identity = _consumer_statement(legacy, seeded["manual"], "identity", display_name="Manual Party")

    legacy.apply(fold_party_handle_statements)
    legacy.apply(fold_party_aspects)

    for kept, aspect in ((handle_claim, "handle"), (identity, "identity")):
        rows = _statements(legacy, seeded["manual"], aspect=aspect)
        assert sorted((row.pk == kept.pk, row.applied) for row in rows) == [(False, False), (True, True)]
    out = capsys.readouterr().out
    assert "1 x handle: another fold's statement is applied; recorded beside it" in out
    assert "1 x Party.identity: another fold's statement is applied; recorded beside it" in out


def _replica_carrier(legacy: LegacyHost, seeded: dict[str, Any]) -> tuple[Any, Any]:
    """A linked replica handle whose import carrier the fold leaves unasserted (no linkedin account channel)."""

    imported, person = seeded["imported"], seeded["person"]
    replica = legacy.create(
        "parties.Handle",
        platform="linkedin",
        value="replica-1",
        normalized_value="replica-1",
        party=imported,
        created_by=person,
    )
    carrier = legacy.create(
        "parties.PartyHandle",
        party=imported,
        handle=replica,
        source="import",
        confidence=1.0,
        created_by=person,
        metadata={"provenance": "social_takeout"},
    )
    legacy.apply(fold_party_handle_statements)
    assert _statements(legacy, imported, handle_id=replica.pk) == []
    return carrier, replica


@pytest.mark.django_db(transaction=True)
def test_the_handle_retirement_accepts_an_import_carrier_another_fold_asserts(
    legacy: LegacyHost, seeded: dict[str, Any], capsys
) -> None:
    """An import carrier the fold leaves unasserted may be a consumer fold's to assert, under its own reason."""

    imported = seeded["imported"]
    carrier, replica = _replica_carrier(legacy, seeded)
    claim = _consumer_statement(legacy, imported, "handle", handle_id=replica.pk)
    legacy.apply(fold_party_aspects)
    legacy.apply(circle_member_decisions)

    legacy.apply(retire_party_handle)

    assert f"PartyHandle {carrier.pk}: unasserted import carrier is asserted by another fold" in capsys.readouterr().out
    assert [row.pk for row in _statements(legacy, imported, handle_id=replica.pk)] == [claim.pk]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    ("history", "activity", "refusal"),
    [(False, "system", "missing folded source or history"), (True, "manual", "is not a fold's system work")],
)
def test_the_handle_retirement_refuses_another_writers_statement_a_fold_did_not_write(
    legacy: LegacyHost, seeded: dict[str, Any], history: bool, activity: str, refusal: str
) -> None:
    """Another writer's statement passes only as a fold writes one: system work, with its native history."""

    carrier, replica = _replica_carrier(legacy, seeded)
    _consumer_statement(legacy, seeded["imported"], "handle", handle_id=replica.pk, history=history, activity=activity)
    legacy.apply(fold_party_aspects)
    legacy.apply(circle_member_decisions)

    with pytest.raises(ValueError, match=f"PartyHandle {carrier.pk}: .*{refusal}"):
        legacy.apply(retire_party_handle)


def test_the_parties_fold_family_names_the_reasons_its_folds_record() -> None:
    """The family declaration keeps literal reasons; the self-contained declarations keep their own copy."""

    assert statement_fold_reasons() == (fold_party_handle_statements.REASON, fold_party_aspects.REASON)


def consumer_fold_reasons() -> tuple[str, ...]:
    """A consumer's fold family (an ``ANGEE_PARTIES_STATEMENT_FOLDS`` entry), as an import's would declare it."""

    return ("fold.consumer",)


@pytest.mark.django_db(transaction=True)
def test_a_migrate_ranks_each_folds_shared_groups_once_and_retries_a_failure(
    composed_tables: None, monkeypatch: pytest.MonkeyPatch, settings: Any
) -> None:
    """A fold's statement applied first keeps a group only until a migrate ranks it, once per fold.

    The consumer's fold ran before the upgrade's: the first migrate's ranking fails and fails
    it, the next one ranks the group, a later one finds the recorded mark and does nothing.
    Then the consumer's fold lands again in a later migrate, beside an applied statement of
    the upgrade's folds: the next migrate ranks that group too. A group only the upgrade's
    folds wrote is left as it is.
    """

    del composed_tables
    settings.ANGEE_PARTIES_STATEMENT_FOLDS = [
        *settings.ANGEE_PARTIES_STATEMENT_FOLDS,
        f"{__name__}.consumer_fold_reasons",
    ]
    statements = Party.statements.model

    def identity(party: Any, name: str, asserted_by: str, **values: Any) -> Any:
        return statements._base_manager.create(
            subject=party, aspect="identity", display_name=name, asserted_by=asserted_by, **values
        )

    with system_context(reason="test folds meet"):
        owner = create_user("folds-meet")
        person = str(to_subject_ref(owner))
        replica = str(to_subject_ref(make_integration("folds-meet-replica", model=Directory)))
        first = Party._base_manager.create(display_name="A. Lovelace", created_by=owner)
        later = Party._base_manager.create(display_name="Grace Hopper", created_by=owner)
        alone = Party._base_manager.create(display_name="Edsger Dijkstra", created_by=owner)
        with changeset(fresh=True, activity="system", change_reason="fold.consumer"):
            consumer = identity(first, "A. Lovelace", replica, source_kind=SourceKind.DIRECTORY, applied=True)
        with changeset(fresh=True, activity="system", change_reason=fold_party_aspects.REASON):
            preferred = identity(
                first, "Ada Lovelace", person, source_kind=SourceKind.PERSON, rank=statements.StatementRank.PREFERRED
            )
            folded = identity(later, "Grace Hopper", person, source_kind=SourceKind.PERSON, applied=True)
            only = identity(alone, "Edsger Dijkstra", person, source_kind=SourceKind.PERSON, applied=True)
    written = statements.history.filter(id=only.pk).count()
    resolve_shared = StatementManager.resolve_shared

    def unavailable(self: Any, changesets: Any) -> int:
        raise RuntimeError("ranking unavailable")

    def applied(*rows: Any) -> list[bool]:
        return [statements._base_manager.get(pk=row.pk).applied for row in rows]

    monkeypatch.setattr(StatementManager, "resolve_shared", unavailable)
    with pytest.raises(RuntimeError, match="ranking unavailable"):
        emit_post_migrate_signal(0, False, connection.alias)
    assert applied(preferred, consumer) == [False, True]

    monkeypatch.setattr(StatementManager, "resolve_shared", resolve_shared)
    emit_post_migrate_signal(0, False, connection.alias)
    assert applied(preferred, consumer) == [True, False]
    assert Party._base_manager.get(pk=first.pk).display_name == "Ada Lovelace"

    monkeypatch.setattr(StatementManager, "resolve_shared", unavailable)
    emit_post_migrate_signal(0, False, connection.alias)  # no fold newer than the recorded mark

    with system_context(reason="test a later consumer fold"):
        # A standing decision the consumer's fold carries over: a preferred stance by its seed principal.
        seed = apps.get_model("provenance", "Seed")._base_manager.create(key="tests.consumer_fold")
        with changeset(fresh=True, activity="system", change_reason="fold.consumer"):
            landed = identity(
                later,
                "G. Hopper",
                f"provenance/seed:{seed.pk}",
                source_kind=SourceKind.SEED,
                rank=statements.StatementRank.PREFERRED,
            )
    monkeypatch.setattr(StatementManager, "resolve_shared", resolve_shared)
    emit_post_migrate_signal(0, False, connection.alias)

    assert applied(landed, folded) == [True, False]
    assert Party._base_manager.get(pk=later.pk).display_name == "G. Hopper"
    assert statements.history.filter(id=only.pk).count() == written


def _created_after_the_preflight() -> frozenset[str]:
    """Every model a chain declaration planned in a later round than the preflight creates.

    None exists when the preflight runs on a database at main, so a preflight that
    looks one up fails there; deriving the set from the chain's own rounds pins every
    future lookup, not only the ones known today. Declarations sharing the preflight's
    round may run on either side of it, and the preflight reads the shape it finds.
    """

    rounds = _rounds(_main_like_state())
    labels = {module: addon.rsplit(".", 1)[1] for addon, modules in CHAIN.items() for module in modules}
    return frozenset(
        f"{labels[module]}.{operation.name}".lower()
        for module, number in rounds.items()
        if number > rounds[preflight_party_folds]
        for operation in module.Migration.operations
        if isinstance(operation, migrations.CreateModel)
    )


@pytest.mark.django_db(transaction=True)
def test_preflight_reads_only_the_pre_upgrade_shape(legacy: LegacyHost, seeded: dict[str, Any]) -> None:
    """The preflight never looks up a model a later round creates: none exists yet at main."""

    hidden = _created_after_the_preflight()
    assert {"parties.historicalparty", "parties.historicalpartystatement"} <= hidden

    class PreUpgradeApps:
        """The legacy host's apps without the models only later declarations create."""

        def __init__(self, apps: Any) -> None:
            self.apps = apps

        def get_model(self, app_label: str, model_name: str | None = None) -> Any:
            label = app_label if model_name is None else f"{app_label}.{model_name}"
            if label.lower() in hidden:
                raise LookupError(label)
            return self.apps.get_model(label)

        def get_models(self) -> Any:
            return [model for model in self.apps.get_models() if model._meta.label_lower not in hidden]

    rows, notices = preflight_party_folds.blockers(PreUpgradeApps(legacy.apps), connection.alias)
    assert rows == []
    problems, _notices = retire_party_carriers.plan(PreUpgradeApps(legacy.apps), connection.alias, folded=False)
    assert problems == []


def test_vcard_lines_ignore_order_and_revision_stamps() -> None:
    assert retire_party_carriers.vcard_lines(VCARD) == retire_party_carriers.vcard_lines(VCARD_REORDERED)
    assert retire_party_carriers.vcard_lines(VCARD) != retire_party_carriers.vcard_lines(VCARD.replace("Olek", "Other"))
    assert retire_party_carriers.vcard_lines("A:1\r\n b\r\nB:2") == ["A:1b", "B:2"]


# --- Chain ordering: the composer's rounds over a database at main's schema ---------------------


def _main_like_state() -> ProjectState:
    """The affected tables as a database at ``main`` holds them, before any lane declaration."""
    state = ProjectState()
    state.add_model(ModelState.from_model(ContentType))
    state.add_model(
        ModelState(
            "spaces",
            "Membership",
            [
                ("id", models.BigAutoField(primary_key=True)),
                ("source", models.CharField(max_length=16, default="manual")),
            ],
        )
    )
    state.add_model(
        ModelState(
            "iam", "User", [("id", models.BigAutoField(primary_key=True)), ("kind", models.CharField(max_length=8))]
        )
    )
    state.add_model(ModelState("integrate", "Integration", [("id", models.BigAutoField(primary_key=True))]))
    state.add_model(
        ModelState(
            "integrate",
            "SyncStream",
            [
                ("id", models.BigAutoField(primary_key=True)),
                ("integration", models.ForeignKey("integrate.Integration", on_delete=models.CASCADE)),
                ("key", models.CharField(max_length=64)),
                ("partition", models.CharField(max_length=512)),
                ("generation", models.PositiveIntegerField(default=1)),
            ],
        )
    )
    link_fields: dict[str, models.Field] = {
        "id": models.BigAutoField(primary_key=True),
        "created_at": models.DateTimeField(),
        "updated_at": models.DateTimeField(),
        "created_by": models.ForeignKey("iam.User", null=True, on_delete=models.SET_NULL, related_name="+"),
        "updated_by": models.ForeignKey("iam.User", null=True, on_delete=models.SET_NULL, related_name="+"),
        "stream": models.ForeignKey("integrate.SyncStream", on_delete=models.CASCADE),
        "parent": models.ForeignKey("integrate.RecordLink", null=True, on_delete=models.SET_NULL, related_name="+"),
        "external_key": models.CharField(max_length=512),
        "target_content_type": models.ForeignKey("contenttypes.ContentType", null=True, on_delete=models.PROTECT),
        "target_object_id": models.CharField(max_length=255, null=True),
        "status": StateField(
            choices=[
                ("current", "Current"),
                ("observed", "Observed"),
                ("unavailable", "Unavailable"),
                ("discrepant", "Discrepant"),
                ("withdrawn", "Withdrawn"),
                ("tombstone", "Tombstone"),
            ],
            default="observed",
            max_length=11,
            db_index=True,
        ),
        "remote_version": models.CharField(max_length=512, blank=True),
        "remote_base_hash": models.CharField(max_length=64, blank=True),
        "local_base_hash": models.CharField(max_length=64, blank=True),
        "origin": models.CharField(max_length=16, blank=True),
        "last_seen_at": models.DateTimeField(null=True),
        "last_verified_generation": models.PositiveIntegerField(default=0),
        "absence_count": models.PositiveIntegerField(default=0),
        "metadata": models.JSONField(default=dict),
        "tombstoned_at": models.DateTimeField(null=True),
    }
    assert set(link_fields) == record_link_history_state.LEGACY_FIELDS
    state.add_model(ModelState("integrate", "RecordLink", list(link_fields.items())))
    state.add_model(
        ModelState(
            "integrate",
            "RecordRevision",
            [
                ("id", models.BigAutoField(primary_key=True)),
                ("link", models.ForeignKey("integrate.RecordLink", on_delete=models.CASCADE)),
                ("number", models.PositiveIntegerField()),
            ],
        )
    )
    state.add_model(ModelState("integrate", "SyncDiscrepancy", [("id", models.BigAutoField(primary_key=True))]))
    state.add_model(ModelState("parties", "Folder", [("id", models.BigAutoField(primary_key=True))]))
    state.add_model(
        ModelState(
            "parties",
            "Party",
            [
                ("id", models.BigAutoField(primary_key=True)),
                ("display_name", models.TextField()),
                ("handle_count", models.PositiveIntegerField(default=0)),
                *_card_fields(constrained=True).items(),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(fields=("folder", "source_uid"), name="uq_party_folder_source_uid")
                ]
            },
        )
    )
    state.add_model(ModelState("parties", "Handle", [("id", models.BigAutoField(primary_key=True))]))
    state.add_model(
        ModelState(
            "parties",
            "PartyHandle",
            [
                ("id", models.BigAutoField(primary_key=True)),
                ("party", models.ForeignKey("parties.Party", on_delete=models.CASCADE)),
                ("handle", models.ForeignKey("parties.Handle", on_delete=models.CASCADE)),
            ],
        )
    )
    state.add_model(
        ModelState(
            "parties",
            "CircleMember",
            [("id", models.BigAutoField(primary_key=True)), ("source", models.CharField(max_length=16))],
        )
    )
    state.add_model(
        ModelState(
            "parties",
            "Relationship",
            [
                ("id", models.BigAutoField(primary_key=True)),
                ("party", models.ForeignKey("parties.Party", on_delete=models.CASCADE)),
                ("other_name", models.TextField(blank=True)),
                ("source", models.CharField(max_length=16)),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(fields=("party", "other_name"), name="uq_relationship_carddav_employment")
                ]
            },
        )
    )
    state.add_model(
        ModelState(
            "parties",
            "Address",
            [
                ("id", models.BigAutoField(primary_key=True)),
                ("party", models.ForeignKey("parties.Party", on_delete=models.CASCADE)),
            ],
        )
    )
    state.add_model(ModelState("parties", "MergeVeto", [("id", models.BigAutoField(primary_key=True))]))
    return state


CHAIN = {
    "angee.provenance": (changeset_schema, statement_principal_schema, retire_reversion),
    "angee.integrate": (
        record_link_history_state,
        record_sync_state,
        fold_record_sync_history,
        retire_record_sync_stores,
        retire_record_sync_hash_bases,
        record_link_failure_status,
    ),
    "angee.spaces": (membership_decisions,),
    "angee.parties": (
        preflight_party_folds,
        party_statement_schema,
        party_aspect_schema,
        fold_party_handle_statements,
        fold_party_aspects,
        circle_member_decisions,
        retire_party_handle,
        retire_party_carriers,
    ),
}
DESTRUCTIVE = (
    retire_reversion,
    retire_record_sync_stores,
    retire_record_sync_hash_bases,
    circle_member_decisions,
    retire_party_handle,
    retire_party_carriers,
)


def _manifest_order(addon: str) -> list[str]:
    manifest = Path(apps.get_app_config(addon.rsplit(".", 1)[1]).path) / "addon.toml"
    return [entry["name"] for entry in tomllib.loads(manifest.read_text())["migrations"]]


def _rounds(state: ProjectState) -> dict[Any, int]:
    """Replay the composer's round algorithm: guards see the state the round starts from."""
    pending = [(addon.rsplit(".", 1)[1], module) for addon, modules in CHAIN.items() for module in modules]
    rounds: dict[Any, int] = {}
    number = 0
    while pending:
        remaining, evaluation = [], state.clone()
        for app_label, module in pending:
            if not module.applies(evaluation.clone()):
                remaining.append((app_label, module))
                continue
            state = module.Migration(f"{number:04d}_{module.__name__.rsplit('.', 1)[1]}", app_label).mutate_state(state)
            rounds[module] = number
        assert len(remaining) < len(pending), f"never applicable: {[m.__name__ for _, m in remaining]}"
        pending, number = remaining, number + 1
    return rounds


def test_chain_declarations_are_in_manifest_order() -> None:
    for addon, modules in CHAIN.items():
        names = [module.__name__.rsplit(".", 1)[1] for module in modules]
        order = _manifest_order(addon)
        assert [name for name in order if name in names] == names


def test_chain_plans_the_preflight_before_every_destructive_declaration_from_main() -> None:
    rounds = _rounds(_main_like_state())
    assert rounds[preflight_party_folds] == 0
    assert all(rounds[module] > rounds[preflight_party_folds] for module in DESTRUCTIVE)
    # spaces drops its roster labels only after the parties preflight round.
    assert rounds[membership_decisions] > rounds[preflight_party_folds]
    assert rounds[changeset_schema] < rounds[statement_principal_schema] < rounds[retire_reversion]
    assert rounds[statement_principal_schema] < rounds[party_statement_schema] <= rounds[fold_party_handle_statements]
    assert (
        rounds[party_aspect_schema]
        < rounds[fold_party_aspects]
        <= rounds[retire_party_handle]
        <= rounds[retire_party_carriers]
    )
    assert rounds[record_link_history_state] < rounds[record_sync_state] < rounds[fold_record_sync_history]
    assert rounds[fold_record_sync_history] <= rounds[retire_record_sync_stores] < rounds[retire_record_sync_hash_bases]
    assert rounds[record_link_history_state] < rounds[record_link_failure_status]


def test_chain_is_complete_and_idle_after_the_fold_from_main() -> None:
    """Every declaration becomes applicable once, and none applies to the migrated state."""
    state = _main_like_state()
    rounds = _rounds(state)
    assert set(rounds) == {module for modules in CHAIN.values() for module in modules}
    final = state
    for module in sorted(rounds, key=lambda module: (rounds[module], str(module))):
        final = module.Migration("0001_x", module.__name__.split(".")[1]).mutate_state(final)
    # retire_reversion's guard stays true by design: its one run is bounded by materialization, not state.
    assert [module.__name__ for module in rounds if module.applies(final.clone())] == [retire_reversion.__name__]
