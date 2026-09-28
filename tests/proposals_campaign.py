"""Campaign data factories over the existing source-addon test composition."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from django.apps import apps
from django.test import override_settings
from django.utils import timezone
from rebac import (
    ObjectRef,
    RelationshipTuple,
    actor_context,
    system_context,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)

from tests.conftest import Backend, Drive
from tests.messaging_models import Person
from tests.projects_models import Milestone, Project, Task
from tests.proposals_models import Answer, Round, Topic
from tests.spaces_models import Group, Membership
from tests.test_project_access import project_access_schema as project_access_schema


def grant(row: Any, relation: str, subject: Any) -> None:
    """Seed a deliberate share using the native portable relationship API."""
    with system_context(reason="tests.proposals.campaign.share"):
        resource = row if isinstance(row, ObjectRef) else to_object_ref(row)
        write_relationships([RelationshipTuple(resource, relation, to_subject_ref(subject))])


def as_actor(row: Any, actor: Any) -> Any:
    """Discard the setup factory's system binding before exercising a user path."""
    return type(row)._base_manager.get(pk=row.pk).with_actor(actor)


@pytest.fixture(params=("denormalized", "registry"))
def campaign_store(request: pytest.FixtureRequest) -> Any:
    """Select the relationship store before the existing schema fixture syncs it."""
    with override_settings(
        REBAC_LOCAL_BACKEND_STORAGE=request.param,
        REBAC_STRICT_MODE=True,
        REBAC_SUPERUSER_BYPASS=False,
    ):
        yield request.param


@pytest.fixture
def campaign_schema(campaign_store: str, project_access_schema: Any) -> None:
    del campaign_store, project_access_schema


class ProposalCampaign:
    """Per-test identities and data, with all behavior delegated to native owners."""

    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.now = datetime(2032, 3, 4, 12, tzinfo=UTC)
        self.people: dict[str, Any] = {}
        self.sequence = 0

    def person(self, seat: str) -> Any:
        if seat not in self.people:
            name = f"{self.prefix}-{seat}"
            with system_context(reason="tests.proposals.campaign.person"):
                user = apps.get_model("iam", "User").objects.create_user(
                    username=name,
                    email=f"{name}@example.test",
                    kind="person",
                )
                Person.objects.for_user(user)
            self.people[seat] = user
        return self.people[seat]

    def round(self, *, policy: str = "drafts_and_tracks", target_kind: str = "project", **fields: Any) -> Any:
        self.sequence += 1
        with system_context(reason="tests.proposals.campaign.round"):
            project = Project.objects.create(title=f"Review target {self.sequence}", owner=self.person("project-owner"))
            milestone = Milestone.objects.create(project=project, name="Questions", sort_order=1024)
            target = project if target_kind == "project" else Task.objects.create(project=project, title="Source")
            return Round.objects.create(
                **{target_kind: target},
                facilitator=self.person("facilitator"),
                name=f"Review {self.sequence}",
                opening_policy=policy,
                last_call_at=self.now + timedelta(days=1),
                submission_deadline=self.now + timedelta(days=2),
                clarifications_shared_until=milestone,
                **fields,
            )

    def admit(self, round: Any, seat: str, *, track: bool = False) -> Any:
        actor = self.person("facilitator")
        with actor_context(actor):
            return as_actor(round, actor).admit(self.person(seat), track=track)

    def answer(self, proposal: Any, *, visibility: str = "round", body: str = "Response") -> Any:
        self.sequence += 1
        with system_context(reason="tests.proposals.campaign.answer"):
            topic = Topic.objects.create(round_id=proposal.round_id, key=f"topic-{self.sequence}", name="Approach")
            return Answer.objects.create(proposal=proposal, topic=topic, body=body, visibility=visibility)

    def team(self, **seats: str) -> Any:
        self.sequence += 1
        with system_context(reason="tests.proposals.campaign.team"):
            group = Group.objects.create(name="Round team", slug=f"{self.prefix}-team-{self.sequence}")
            for seat, role in seats.items():
                Membership.objects.create(
                    group=group,
                    party=Person.objects.for_user(self.person(seat)),
                    role=role,
                    is_confirmed=True,
                )
            return group

    def open(self, round: Any) -> Any:
        actor = self.person("facilitator")
        with actor_context(actor):
            return as_actor(round, actor).open()

    def ask(self, round: Any, *, seat: str = "responder", **kwargs: Any) -> Any:
        actor = self.person(seat)
        with actor_context(actor):
            return as_actor(round, actor).ask("Question", "Question details", **kwargs)


@pytest.fixture
def campaign(
    campaign_schema: None,
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    settings: Any,
) -> ProposalCampaign:
    """Use worker-local Django isolation, a fixed clock and per-test user names."""
    del campaign_schema
    prefix = "pc-" + hashlib.sha256(request.node.nodeid.encode()).hexdigest()[:12]
    data = ProposalCampaign(prefix)
    monkeypatch.setattr(timezone, "now", lambda: data.now)
    with system_context(reason="tests.proposals.campaign.storage"):
        backend = Backend.objects.create(slug=f"{prefix}-backend", backend_class="local")
        drive = Drive.objects.create(slug=f"{prefix}-default", name="Default", prefix=prefix, backend=backend)
    settings.ANGEE_STORAGE_DEFAULT_DRIVE = drive.slug
    return data
