"""Messaging campaign fixtures on the suite's canonical source compositions."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from rebac import RelationshipTuple, system_context, to_object_ref, to_subject_ref, write_relationships

from angee.graphql.publishing import mute_changes
from angee.messaging.models import AudienceMember, NotificationPolicy
from tests.chatterdemo.models import ChatterDoc
from tests.messaging_models import Message, Party, Person, ThreadAttachment, ThreadNotification
from tests.projects_models import Project
from tests.spaces_models import Group, Membership


def grant(row, relation, user):
    """Grant an explicit test share using the native relationship API."""
    with system_context(reason="test.messaging.campaign.share"):
        write_relationships([
            RelationshipTuple(resource=to_object_ref(row), relation=relation, subject=to_subject_ref(user)),
        ])


def make_user(name):
    """Create accounts without password hashing or transport work."""
    with system_context(reason="test.messaging.campaign.account"):
        return get_user_model().objects.create_user(username=f"{name}-{uuid4().hex}")


@pytest.fixture
def audience_record(spaces_tables, monkeypatch):
    """Exercise declarations on existing project columns and tables.

    This test-owned donor names ``team`` and ``lead`` solely to exercise messaging's
    extension contract; it does not stand in for the unmerged task-assignee owner.
    All membership, identity, permissions, and delivery behavior stays real.
    """
    def named_members(record):
        if record.lead_id is None:
            return ()
        person = Person._base_manager.get(user_id=record.lead_id)
        return (AudienceMember(party_id=person.pk, notification_policy=NotificationPolicy.INBOX),)

    monkeypatch.setattr(Project, "thread_team_field", "team")
    monkeypatch.setattr(Project, "thread_audience_members", named_members)
    with system_context(reason="test.messaging.campaign.audience"), mute_changes():
        author = make_user("audience-author")
        recipient = make_user("audience-recipient")
        party = Party.objects.for_user(recipient)
        team = Group.objects.create(name="Campaign team", slug="campaign-team")
        record = Project.objects.create(title="Campaign record", team=team, created_by=author)
        attachment = ThreadAttachment.objects.ensure_for_record(record)
    return SimpleNamespace(author=author, recipient=recipient, party=party, team=team,
                           record=record, attachment=attachment)


def add_member(case, *, policy="inbox", **values):
    """Use the live spaces roster; no follower mirror is created."""
    with system_context(reason="test.messaging.campaign.roster"):
        return Membership.objects.create(group=case.team, party=case.party, is_confirmed=True,
                                         notification_policy=policy, **values)


def fanout(case, *, direct=False):
    """Create one message and call the same delivery owner as posting."""
    with system_context(reason="test.messaging.campaign.fanout"), mute_changes():
        message = Message.objects.create(thread=case.attachment.thread, message_type="comment",
                                         direction="internal", created_by=case.author)
        ThreadNotification.objects.fanout_for_message(
            message, attachment=case.attachment, created_by_id=case.author.pk,
            recipient_user_ids=(case.recipient.pk,) if direct else (),
        )
    return message


@pytest.fixture
def comment_record(composed_tables, monkeypatch):
    """A real REBAC record with read-only commenters and separate writers."""
    monkeypatch.setattr(ChatterDoc, "thread_post_access", "read")
    with system_context(reason="test.messaging.campaign.comment"), mute_changes():
        author = make_user("comment-author")
        peer = make_user("comment-peer")
        writer = make_user("comment-writer")
        outsider = make_user("comment-outsider")
        record = ChatterDoc.objects.create(title="Comment contract")
    grant(record, "reader", author)
    grant(record, "reader", peer)
    grant(record, "writer", writer)
    return SimpleNamespace(record=record, author=author, peer=peer, writer=writer, outsider=outsider)
