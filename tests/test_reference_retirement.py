"""Source retirement composes history ownership and declared live references."""

import pytest
from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.db.models import Q
from rebac import RelationshipTuple, system_context, to_object_ref, to_subject_ref, write_relationships
from simple_history.utils import get_history_manager_for_model

from angee.base.models.merge_references import has_live_references, named_in_relationships, owned_reference_parts
from angee.provenance.context import changeset
from angee.provenance.statements import SourceKind
from tests.conftest import create_user
from tests.mtidemo.models import MtiChild, MtiParent
from tests.recordrefdemo.models import RecordRefSubjectEdge

pytestmark = pytest.mark.usefixtures("composed_tables")


@pytest.mark.parametrize(
    "case", ["child-creation", "parent-edit", "child-edit", "foreign-origin", "unattributed", "no-creation", "empty"]
)
def test_history_written_only_by_requires_creation_and_every_table(case):
    owner = create_user("retirement-history")
    Party = apps.get_model("parties", "Party")
    Organization = apps.get_model("parties", "Organization")
    Changeset = apps.get_model("provenance", "Changeset")
    with system_context(reason="test retirement history"):
        with changeset(fresh=True, activity="pull", origin=owner):
            record = Organization.objects.create(display_name="Source", legal_name="Source Ltd", created_by=owner)
        # The child's creation alone must count, as it does in native MTI writes.
        Party.history.filter(id=record.pk).delete()
        child_history = get_history_manager_for_model(Organization).filter(
            **{Organization._meta.pk.attname: record.pk},
        )
        assert child_history.filter(history_type="+").exists()
        predicate = Q(
            activity="pull",
            origin_content_type=ContentType.objects.get_for_model(owner),
            origin_object_id__in=[str(owner.pk)],
        )
        assert Changeset.objects.history_written_only_by(record, predicate)
        if case in {"parent-edit", "child-edit", "foreign-origin"}:
            origin = create_user("retirement-foreign-origin") if case == "foreign-origin" else owner
            with changeset(fresh=True, activity="pull" if case == "foreign-origin" else "manual", origin=origin):
                edited = Party.objects.get(pk=record.pk) if case == "parent-edit" else record
                edited.display_name = "Human edit"
                edited.save(update_fields=["display_name"])
        elif case == "unattributed":
            child_history.update(changeset_id=None)
        elif case == "no-creation":
            child_history.update(history_type="~")
        elif case == "empty":
            child_history.delete()
        assert Changeset.objects.history_written_only_by(record, predicate) == (case == "child-creation")
        assert not Changeset.objects.history_written_only_by(record, Q(activity="push"))
        assert not Changeset.objects.history_written_only_by(MtiParent(), predicate)


def test_history_written_only_by_includes_owned_lines_and_respects_queryset():
    owner = create_user("retirement-lines")
    Project = apps.get_model("projects", "Project")
    Milestone = apps.get_model("projects", "Milestone")
    Changeset = apps.get_model("provenance", "Changeset")
    with system_context(reason="test retirement document"):
        with changeset(fresh=True, activity="pull", origin=owner):
            project = Project.objects.create(title="Imported", owner=owner)
            line = Milestone.objects.create(project=project, name="Delivery")
        assert list(owned_reference_parts(project)) == [line]
        assert not has_live_references(project)
        edge = RecordRefSubjectEdge.objects.create(
            subject_content_type=ContentType.objects.get_for_model(Milestone), subject_object_id=line.pk,
        )
        assert has_live_references(project)
        edge.delete()
        assert not has_live_references(project)
        matching = Q(activity="pull", origin_object_id=str(owner.pk))
        assert Changeset.objects.history_written_only_by(project, matching)
        assert not Changeset.objects.none().history_written_only_by(project, matching)
        with changeset(fresh=True, activity="manual"):
            line.name = "Retained local edit"
            line.save(update_fields=["name"])
        assert not Changeset.objects.history_written_only_by(project, matching)


@pytest.mark.parametrize("on_part", [False, True])
@pytest.mark.parametrize(
    "foreign,status,superseded,expected",
    [
        (False, "withdrawn", False, False),
        (False, "asserted", False, True),
        (False, "quarantined", False, True),
        (False, "deprecated", False, True),
        (False, "asserted", True, False),
        (True, "withdrawn", False, True),
        (True, "withdrawn", True, True),
    ],
)
def test_live_references_preserve_current_and_foreign_statements(on_part, foreign, status, superseded, expected):
    owner = create_user("retirement-author")
    other = create_user("retirement-other")
    principal = to_subject_ref(owner)
    Party = apps.get_model("parties", "Party")
    Address = apps.get_model("parties", "Address")
    with system_context(reason="test live statement references"):
        party = Party._base_manager.create(display_name="Imported", created_by=owner)
        address = Address._base_manager.create(party=party, label="Imported address", created_by=owner)
        assert list(owned_reference_parts(party)) == [address]
        assert not has_live_references(party, principal=principal)
        subject = address if on_part else party
        statements = subject.statements.model
        aspect = next(iter(statements.statement_aspects))
        replacement = None
        if superseded:
            replacement = statements._base_manager.create(
                subject=subject,
                aspect=aspect,
                asserted_by=str(principal),
                source_kind=SourceKind.PERSON,
                status="withdrawn",
            )
        statements._base_manager.create(
            subject=subject,
            aspect=aspect,
            asserted_by=str(to_subject_ref(other) if foreign else principal),
            source_kind=SourceKind.PERSON,
            status=status,
            superseded=replacement,
        )
        assert has_live_references(party, principal=principal) == expected
        assert has_live_references(party)  # No principal means no authorship exclusion.
        assert Address._base_manager.filter(pk=address.pk).exists()


def test_live_references_include_owned_part_callers_and_ignore_nonblocking_history():
    owner = create_user("retirement-parts")
    Party = apps.get_model("parties", "Party")
    Address = apps.get_model("parties", "Address")
    with system_context(reason="test owned reference parts"):
        party = Party._base_manager.create(display_name="Imported", created_by=owner)
        address = Address._base_manager.create(party=party, label="Imported", created_by=owner)
        assert address.history.exists()
        assert not has_live_references(party)
        edge = RecordRefSubjectEdge.objects.create(
            subject_content_type=ContentType.objects.get_for_model(Address),
            subject_object_id=address.pk,
        )
        assert has_live_references(party)
        edge.delete()
        write_relationships([RelationshipTuple(to_object_ref(address), "editor", to_subject_ref(owner))])
        assert has_live_references(party)
        assert not has_live_references(party, exclude_relations=("editor",))


def test_live_reference_relation_exclusion_keeps_other_mti_shares():
    owner = create_user("retirement-mti")
    with system_context(reason="test reference relation exclusions"):
        child = MtiChild.objects.create(title="Shared", detail="Child")
        parent = MtiParent._base_manager.get(pk=child.pk)
        write_relationships([RelationshipTuple(to_object_ref(child), "reader", to_subject_ref(owner))])
        assert named_in_relationships(parent)
        assert not named_in_relationships(parent, exclude=("reader",))
        assert has_live_references(parent)
        assert not has_live_references(parent, exclude_relations=("reader",))
        assert has_live_references(parent, exclude_relations=("unrelated",))
