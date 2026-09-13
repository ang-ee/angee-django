"""Focused contracts for the generic direct record-sharing surface."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest
from django.core.exceptions import ValidationError
from rebac import SubjectRef
from rebac.resources import model_for_resource_type
from rebac.schema.parser import parse_zed

from angee.base import models as base_models
from angee.base.identity import public_id_for
from angee.graphql import sharing
from angee.graphql.data import metadata
from angee.graphql.sharing import RecordAccessType
from angee.storage.models import Drive
from angee.workflows.models import Workflow


def test_group_access_projects_canonical_subject_identity() -> None:
    access = SimpleNamespace(
        relation="viewer",
        subject=SubjectRef.of("auth/group", "42", "member"),
    )

    projected = RecordAccessType.from_direct("row_1", access, "Reviewers")

    assert projected.target_id == "row_1"
    group_model = model_for_resource_type("auth/group")
    assert group_model is not None
    assert projected.subject == f"auth/group:{public_id_for(group_model, 42)}#member"
    assert projected.subject_type == "auth/group"
    assert projected.label == "Reviewers"


def test_share_declarations_and_lineage_head_guard() -> None:
    assert Drive.get_rebac_grantable() == {"editor": "write", "viewer": "write"}
    assert Workflow.get_rebac_grantable() == {"editor": "write", "viewer": "write"}
    Workflow.validate_record_access_target(SimpleNamespace(published_from_id=None))
    with pytest.raises(ValidationError, match="lineage head"):
        Workflow.validate_record_access_target(SimpleNamespace(published_from_id=7))


@pytest.mark.parametrize("relation,selectable", [("member", True), ("owner", False)])
def test_subject_picker_resource_matches_the_declared_relation(
    monkeypatch: pytest.MonkeyPatch, relation: str, selectable: bool,
) -> None:
    """A member-subject picker cannot submit another group subject relation."""

    group_model = model_for_resource_type("auth/group")
    assert group_model is not None
    definition = parse_zed(
        "definition storage/drive {\n"
        f"    relation viewer: auth/group#{relation}\n"
        "    permission write = viewer\n"
        "}\n"
    ).get_definition("storage/drive")
    monkeypatch.setattr(metadata, "effective_rebac_definition", lambda model: definition)
    resource = SimpleNamespace(model_label="iam.Group", subject_field="assignment_subject")

    grantable = metadata._grantable_relations(Drive, {group_model: cast(Any, resource)})

    assert len(grantable) == 1
    subject = grantable[0].subjects[0]
    assert subject.relation == relation
    assert subject.resource == ("iam.Group" if selectable else None)


@pytest.mark.parametrize(
    "backing",
    ("field=created_by", "const=admin", 'attribute={"field":"kind"}'),
)
def test_backed_share_relation_reports_a_system_check_error(
    monkeypatch: pytest.MonkeyPatch, backing: str,
) -> None:
    """Native backing objects need no kind attribute to reject a grant surface."""

    definition = parse_zed(
        "definition storage/drive {\n"
        f"    relation owner: auth/user // rebac:{backing}\n"
        "    permission write = owner\n"
        "}\n"
    ).get_definition("storage/drive")
    monkeypatch.setattr(Drive, "rebac_grantable", {"owner": "write"})
    monkeypatch.setattr(base_models, "effective_rebac_definition", lambda model: definition)

    errors = Drive._check_rebac_grantable()

    assert [error.id for error in errors] == ["angee.E016"]
    assert "backed relations cannot be granted" in errors[0].msg


def test_relation_options_are_authorized_independently(monkeypatch: pytest.MonkeyPatch) -> None:
    target = SimpleNamespace(validate_record_access_target=lambda: None)

    class ShareModel:
        @classmethod
        def get_rebac_grantable(cls) -> dict[str, str]:
            return {"viewer": "share", "editor": "admin"}

    def authorize(_info: Any, _model: Any, _id: Any, permission: str) -> Any:
        if permission == "admin":
            raise ValidationError("denied")
        return target

    monkeypatch.setattr(sharing, "authorized_permission_target", authorize)

    resolved, allowed = sharing._authorized_record_access(None, ShareModel, "row_1")

    assert resolved is target
    assert allowed == ["viewer"]


def test_relation_options_require_authority_on_every_selected_record(monkeypatch: pytest.MonkeyPatch) -> None:
    """A mixed selection cannot offer a relation authorized on just one target."""

    target = SimpleNamespace(validate_record_access_target=lambda: None)

    class ShareModel:
        @classmethod
        def get_rebac_grantable(cls) -> dict[str, str]:
            return {"viewer": "share", "editor": "admin"}

    def authorize(_info: Any, _model: Any, target_id: str, permission: str) -> Any:
        if target_id == "limited" and permission == "admin":
            raise ValidationError("denied")
        return target

    monkeypatch.setattr(sharing, "_shareable_model", lambda _type: ShareModel)
    monkeypatch.setattr(sharing, "authorized_permission_target", authorize)

    options = sharing.RecordAccessQuery().record_access_options(None, "test/record", ["full", "limited"])

    assert [(option.relation, option.permission) for option in options] == [("viewer", "share")]
    with pytest.raises(ValueError, match="at least one"):
        sharing.RecordAccessQuery().record_access_options(None, "test/record", [])


@pytest.mark.parametrize(
    "subject",
    (SubjectRef.of("angee/role", "admin", "member"), SubjectRef.of("auth/user", "*")),
)
def test_legacy_or_wildcard_subjects_remain_visible(subject: SubjectRef) -> None:
    projected = RecordAccessType.from_direct(
        "row_1",
        SimpleNamespace(relation="viewer", subject=subject),
        str(subject),
    )

    assert projected.subject == str(subject)
    assert projected.subject_type == subject.subject_type
