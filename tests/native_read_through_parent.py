"""Children and labels REBAC reads with their parent under ``ANGEE_REBAC_READ_THROUGH_PARENT``, on the composed policy.

``DELIVERED_EDGES`` pins every foreign key along which REBAC's structural prefetch
reads a child's ``read`` without its permission statement, with the parent
permissions under which it does, as REBAC lists them for the composed policy
(``rebac.structural.edges``). ``EDGE_FIELDS`` pins the
GraphQL fields that read those children from a parent row, on the schema or in a
resolver's own prefetch; an edge no field reads has none. A policy, library or
schema change that moves either fails here until someone has looked.

The differential reads every such field that a root lists, and every pinned
resolver, as a platform admin, a member who owns the rows and an actor granted one
parent of each kind alone, with the switch off and on: the same data, and the
edges ``GRAPHQL_DELIVERED`` names delivered (read without their permission
statement) to every actor with the switch on and to none with it off.
The withheld cases seed a child its parent's reader may not read along an edge
REBAC refuses, and require it withheld with the switch on; a stored override stops
every delivery. Every composed test that turns the switch on fails when REBAC
delivers along an edge no pinned field reads (``assert_read_by_pinned_fields``).

``DISPLAY_FIELDS`` pins the models that declare display fields, a referred record's
label; ``DISPLAY_AUDIENCE`` every GraphQL field that can show them and
``HOLDER_READERS`` who reads each holder (``angee.graphql.relations.display_audience``).
The label differential reads each pinned field a root lists for its target's key and
label, off and on, and requires the same data but where the reader may not read the
record, as ``LABEL_SHOWN`` names; every composed test that turns the switch on fails
when display rows are asked for a model the pin does not list
(``assert_labels_through_pinned_fields``).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.db import models
from django.test import TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from graphql import GraphQLEnumType, GraphQLList, GraphQLNonNull, GraphQLObjectType, GraphQLScalarType
from rebac import (
    RelationshipTuple,
    actor_context,
    structural,
    system_context,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)
from rebac.managers import RebacQuerySet

from angee.base.models.identity import public_id_of
from angee.graphql import relations
from angee.graphql.schema import GraphQLSchemas
from angee.graphql.testing.documents import execute_schema
from angee.iam.permissions import is_platform_admin
from angee.storage.uploads import DownloadTokenSigner
from tests import native_graphql_document_budgets as budgets

# The switch's effects, as base autoconfig derives them.
READ_THROUGH_PARENT = budgets.READ_THROUGH_PARENT

# "<child model>.<foreign key> -> <parent model>": the parent permissions under which REBAC reads the
# child's ``read`` with the parent, without its permission statement (``rebac.structural.edges``;
# a field gate's name among them admits no row, and is listed as REBAC lists it).
DELIVERED_EDGES: dict[str, tuple[str, ...]] = {
    "agents.AgentTurn.session -> agents.AgentSession": ("create", "post", "read", "write"),
    "agents.InferenceModel.provider -> agents.InferenceProvider": ("create", "delete", "read", "write", "write__owner"),
    "agents.InferenceProvider.integration_ptr -> integrate.Integration": (
        "create", "delete", "read", "resume", "retry_discovery", "transfer", "write", "write__owner",
    ),
    "agents.MCPTool.server -> agents.MCPServer": ("create", "delete", "read", "write"),
    "agents.Skill.source -> integrate_vcs.Source": ("create", "delete", "read", "write"),
    "calendar.CalendarEvent.calendar -> calendar.Calendar": ("delete", "share", "transfer", "write", "write__owner"),
    "calendar.CalendarSource.integration_ptr -> integrate.Integration": (
        "create", "delete", "read", "resume", "retry_discovery", "transfer", "write", "write__owner",
    ),
    "calendar.EventAttendee.event -> calendar.CalendarEvent": ("create", "delete", "read", "read_granted", "write"),
    "dashboards.DashboardWidget.dashboard -> dashboards.Dashboard": (
        "delete", "read", "reset", "share", "transfer", "write__is_baseline", "write__owner",
    ),
    "decisions.DecisionRecord.decision -> decisions.Decision": ("create", "delete", "read", "read_granted", "write"),
    "extraction.Extraction.file -> storage.File": ("delete", "owner_writer", "read", "transfer", "write__owner"),
    "extraction.Extraction.message -> messaging.Message": (
        "annotate", "identity_source", "read", "read_inbox", "send_held", "withheld_keeper",
    ),
    "extraction.ExtractionSource.extraction -> extraction.Extraction": (
        "read", "read__document_map", "read__outcome", "read__result", "read__retired_identities", "share",
    ),
    "intake.Need.project -> projects.Project": (
        "delete", "read", "share", "transfer", "write", "write__owner", "write__owns_items", "write__team",
    ),
    "intake.Need.task -> projects.Task": ("concealed_keeper", "read", "subject_keeper"),
    "integrate.RecordLink.stream -> integrate.SyncStream": ("create", "delete", "read", "write"),
    "integrate.SyncStream.integration -> integrate.Integration": (
        "create", "delete", "read", "resume", "retry_discovery", "transfer", "write", "write__owner",
    ),
    "integrate_vcs.Repository.vcs_bridge -> integrate_vcs.VcsBridge": (
        "create", "delete", "read", "write", "write__owner",
    ),
    "integrate_vcs.Source.repository -> integrate_vcs.Repository": ("create", "delete", "read", "write"),
    "integrate_vcs.Template.source -> integrate_vcs.Source": ("create", "delete", "read", "write"),
    "integrate_vcs.VcsBridge.integration_ptr -> integrate.Integration": (
        "create", "delete", "read", "resume", "retry_discovery", "transfer", "write", "write__owner",
    ),
    "knowledge.Link.source_page -> knowledge.Page": ("read",),
    "knowledge.MarkdownPage.page_ptr -> knowledge.Page": ("read",),
    "knowledge.Page.vault -> knowledge.Vault": ("delete", "page_delete", "transfer", "write__owner"),
    "knowledge_graph_pgvector.EntityEdge.passage -> knowledge_graph_pgvector.Passage": ("graph_read",),
    "knowledge_graph_pgvector.EntityMention.passage -> knowledge_graph_pgvector.Passage": ("graph_read",),
    "knowledge_graph_pgvector.Passage.page -> knowledge.Page": ("read",),
    "knowledge_graph_pgvector.PassageEmbedding.passage -> knowledge_graph_pgvector.Passage": ("read",),
    "knowledge_integrate_notion.NotionConnection.integration_ptr -> integrate.Integration": (
        "create", "delete", "read", "resume", "retry_discovery", "transfer", "write", "write__owner",
    ),
    "messaging.Channel.integration_ptr -> integrate.Integration": (
        "create", "delete", "read", "resume", "retry_discovery", "transfer", "write", "write__owner",
    ),
    "messaging.Message.channel -> messaging.Channel": ("create", "delete", "write", "write__owner", "write__team"),
    "messaging.MessageStar.message -> messaging.Message": (
        "annotate", "identity_source", "read", "read_inbox", "send_held", "withheld_keeper",
    ),
    "messaging.Part.message -> messaging.Message": (
        "annotate", "identity_source", "read", "read_inbox", "send_held", "withheld_keeper",
    ),
    "messaging.Participant.message -> messaging.Message": (
        "annotate", "identity_source", "read", "read_inbox", "send_held", "withheld_keeper",
    ),
    "messaging.Participant.thread -> messaging.Thread": (
        "annotate", "chatter_read", "delete", "identity_source", "read", "read_granted", "read_inbox", "share",
        "transfer", "write", "write__owner",
    ),
    "messaging.Reaction.message -> messaging.Message": (
        "annotate", "identity_source", "read", "read_inbox", "send_held", "withheld_keeper",
    ),
    "messaging.Thread.channel -> messaging.Channel": (
        "create", "delete", "read", "reply", "write", "write__owner", "write__team",
    ),
    "messaging.ThreadAttachment.thread -> messaging.Thread": (
        "annotate", "chatter_read", "delete", "identity_source", "read", "read_granted", "read_inbox", "share",
        "transfer", "write", "write__owner",
    ),
    "messaging.ThreadFollower.attachment -> messaging.ThreadAttachment": ("read", "write"),
    "messaging.ThreadFollower.thread -> messaging.Thread": (
        "annotate", "chatter_read", "delete", "identity_source", "read", "read_granted", "read_inbox", "share",
        "transfer", "write", "write__owner",
    ),
    "messaging.ThreadNotification.attachment -> messaging.ThreadAttachment": ("read", "write"),
    "messaging.ThreadNotification.message -> messaging.Message": (
        "annotate", "identity_source", "read", "read_inbox", "send_held", "withheld_keeper",
    ),
    "messaging.ThreadNotification.thread -> messaging.Thread": (
        "annotate", "chatter_read", "delete", "identity_source", "read", "read_granted", "read_inbox", "share",
        "transfer", "write", "write__owner",
    ),
    "parties.Address.party -> parties.Party": (
        "role_admin", "role_adopt", "role_delete", "role_manage", "role_merge_away", "role_private", "role_read",
        "role_share", "role_write_as_subject",
    ),
    "parties.CircleMember.party -> parties.Party": (
        "role_admin", "role_adopt", "role_delete", "role_manage", "role_merge_away", "role_private", "role_read",
        "role_share", "role_write_as_subject",
    ),
    "parties.Directory.integration_ptr -> integrate.Integration": (
        "create", "delete", "read", "resume", "retry_discovery", "transfer", "write", "write__owner",
    ),
    "parties.Handle.party -> parties.Party": (
        "role_admin", "role_adopt", "role_delete", "role_manage", "role_merge_away", "role_private", "role_read",
        "role_share", "role_write_as_subject",
    ),
    "parties.Organization.party_ptr -> parties.Party": (
        "contact_read", "role_admin", "role_admin_any", "role_adopt", "role_delete", "role_identity", "role_manage",
        "role_manage_any", "role_merge_away", "role_private", "role_read", "role_share", "role_write_as_subject",
    ),
    "parties.Person.party_ptr -> parties.Party": (
        "contact_read", "role_admin", "role_admin_any", "role_adopt", "role_delete", "role_identity", "role_manage",
        "role_manage_any", "role_merge_away", "role_private", "role_read", "role_share", "role_write_as_subject",
    ),
    "parties.Relationship.party -> parties.Party": (
        "role_admin", "role_adopt", "role_delete", "role_manage", "role_merge_away", "role_private", "role_read",
        "role_share", "role_write_as_subject",
    ),
    "posts.Feed.channel_ptr -> messaging.Channel": (
        "create", "delete", "read", "reply", "write", "write__owner", "write__team",
    ),
    "posts.PostMetrics.message -> messaging.Message": (
        "annotate", "identity_source", "read", "read_inbox", "send_held", "withheld_keeper",
    ),
    "posts.Quota.integration -> integrate.Integration": (
        "create", "delete", "read", "resume", "retry_discovery", "transfer", "write", "write__owner",
    ),
    "projects.Milestone.project -> projects.Project": (
        "delete", "read", "share", "transfer", "write", "write__owner", "write__owns_items", "write__team",
    ),
    "projects.Participant.project -> projects.Project": (
        "delete", "read", "share", "transfer", "write", "write__owner", "write__owns_items", "write__team",
    ),
    "projects.Project.team -> spaces.Group": ("delete", "manage_roster", "post", "transfer", "write", "write__owner"),
    "projects.ProjectBinding.project -> projects.Project": (
        "delete", "read", "share", "transfer", "write", "write__owner", "write__owns_items", "write__team",
    ),
    "proposals.Answer.proposal -> proposals.Proposal": ("create", "manage", "share"),
    "proposals.Proposal.round -> proposals.Round": (
        "delete", "manage", "read__clarification_askers", "read__roster_visibility", "share", "write",
    ),
    "proposals.Review.proposal -> proposals.Proposal": ("create", "evaluate", "manage", "share"),
    "proposals.Round.team -> spaces.Group": ("delete", "manage_roster", "post", "transfer", "write", "write__owner"),
    "proposals.Topic.round -> proposals.Round": (
        "delete", "manage", "read", "read__clarification_askers", "read__roster_visibility", "share", "write",
    ),
    "spaces.Membership.group -> spaces.Group": (
        "delete", "manage_roster", "post", "read", "transfer", "write", "write__owner",
    ),
    "storage.FileAttachment.file -> storage.File": ("delete", "owner_writer", "read", "transfer", "write__owner"),
    "storage.Folder.drive -> storage.Drive": (
        "create", "delete", "read", "share", "transfer", "write", "write__owner", "write__owns_items",
    ),
    "storage_integrate.Mount.integration_ptr -> integrate.Integration": (
        "create", "delete", "read", "resume", "retry_discovery", "transfer", "write", "write__owner",
    ),
    "webforms.FormAlias.form -> webforms.Form": ("create", "delete", "read", "write"),
    "webforms.FormVersion.form -> webforms.Form": ("create", "delete", "read", "write"),
    "webforms.Response.form -> webforms.Form": ("create", "delete", "read", "write"),
    "webforms.ResponseRecord.response -> webforms.Response": ("create", "delete", "read", "write"),
    "work.Cycle.queue -> work.Queue": ("delete", "read", "share", "withheld", "write", "write__owner"),
    "work.Queue.group_ptr -> spaces.Group": ("delete", "manage_roster", "transfer", "write", "write__owner"),
    "work.Stage.queue -> work.Queue": ("delete", "read", "share", "withheld", "write", "write__owner"),
    "workflows.StepAttempt.step_run -> workflows.StepRun": ("create", "delete", "read", "write"),
    "workflows.StepRecord.run -> workflows.WorkflowRun": ("read",),
    "workflows.StepRun.run -> workflows.WorkflowRun": ("read",),
    "workflows.StepWatch.step_run -> workflows.StepRun": ("create", "delete", "read", "write"),
    "workflows.Trigger.workflow -> workflows.Workflow": (
        "create", "delete", "monitor", "read", "read__draft", "read__draft_revision", "read__layout", "transfer",
        "write", "write__owner",
    ),
    "workflows.TriggerEvent.trigger -> workflows.Trigger": ("create", "delete", "write"),
    "workflows.WorkflowVersion.workflow -> workflows.Workflow": (
        "create", "delete", "monitor", "read", "read__draft", "read__draft_revision", "read__layout", "transfer",
        "write", "write__owner",
    ),
}

# The same edges: the GraphQL fields that read their children from a parent row, "<schema> <Type>.<field>",
# found on the schema (``edge_fields``) or, for a resolver's own prefetch, in ``RESOLVER_PREFETCHES``;
# an edge no field reads has none.
EDGE_FIELDS: dict[str, tuple[str, ...]] = {
    "agents.AgentTurn.session -> agents.AgentSession": (),
    "agents.InferenceModel.provider -> agents.InferenceProvider": (),
    "agents.InferenceProvider.integration_ptr -> integrate.Integration": (),
    "agents.MCPTool.server -> agents.MCPServer": (),
    "agents.Skill.source -> integrate_vcs.Source": (),
    "calendar.CalendarEvent.calendar -> calendar.Calendar": (),
    "calendar.CalendarSource.integration_ptr -> integrate.Integration": (),
    "calendar.EventAttendee.event -> calendar.CalendarEvent": (),
    "dashboards.DashboardWidget.dashboard -> dashboards.Dashboard": ("console Query.dashboard_summaries",),
    "decisions.DecisionRecord.decision -> decisions.Decision": (
        "console DecisionType.records", "public DecisionType.records",
    ),
    "extraction.Extraction.file -> storage.File": (),
    "extraction.Extraction.message -> messaging.Message": (),
    "extraction.ExtractionSource.extraction -> extraction.Extraction": ("console ExtractionType.sources",),
    "intake.Need.project -> projects.Project": (),
    "intake.Need.task -> projects.Task": (),
    "integrate.RecordLink.stream -> integrate.SyncStream": (
        "console SyncStreamType.failures", "console SyncStreamType.open_failure_count",
    ),
    "integrate.SyncStream.integration -> integrate.Integration": (
        "console CalendarSourceType.open_failure_count", "console ChannelType.open_failure_count",
        "console DirectoryType.open_failure_count", "console FeedType.open_failure_count",
        "console MountType.open_failure_count", "console NotionConnectionType.open_failure_count",
        "console VcsBridgeType.open_failure_count", "public ChannelType.open_failure_count",
        "public DirectoryType.open_failure_count",
    ),
    "integrate_vcs.Repository.vcs_bridge -> integrate_vcs.VcsBridge": (),
    "integrate_vcs.Source.repository -> integrate_vcs.Repository": (),
    "integrate_vcs.Template.source -> integrate_vcs.Source": (),
    "integrate_vcs.VcsBridge.integration_ptr -> integrate.Integration": (),
    "knowledge.Link.source_page -> knowledge.Page": ("console PageType.wikilinks", "public PageType.wikilinks"),
    "knowledge.MarkdownPage.page_ptr -> knowledge.Page": (
        "console PageType.kind", "console PageType.markdown", "public PageType.kind", "public PageType.markdown",
    ),
    "knowledge.Page.vault -> knowledge.Vault": (),
    "knowledge_graph_pgvector.EntityEdge.passage -> knowledge_graph_pgvector.Passage": (),
    "knowledge_graph_pgvector.EntityMention.passage -> knowledge_graph_pgvector.Passage": (),
    "knowledge_graph_pgvector.Passage.page -> knowledge.Page": (),
    "knowledge_graph_pgvector.PassageEmbedding.passage -> knowledge_graph_pgvector.Passage": (),
    "knowledge_integrate_notion.NotionConnection.integration_ptr -> integrate.Integration": (),
    "messaging.Channel.integration_ptr -> integrate.Integration": (),
    "messaging.Message.channel -> messaging.Channel": (),
    "messaging.MessageStar.message -> messaging.Message": ("console MessageType.starred", "public MessageType.starred"),
    "messaging.Part.message -> messaging.Message": ("console MessageType.parts", "public MessageType.parts"),
    "messaging.Participant.message -> messaging.Message": (
        "console MessageType.participants", "public MessageType.participants",
    ),
    "messaging.Participant.thread -> messaging.Thread": (
        "console ThreadType.participants", "public ThreadType.participants",
    ),
    "messaging.Reaction.message -> messaging.Message": (
        "console MessageType.reaction_groups", "public MessageType.reaction_groups",
    ),
    "messaging.Thread.channel -> messaging.Channel": (),
    "messaging.ThreadAttachment.thread -> messaging.Thread": (),
    "messaging.ThreadFollower.attachment -> messaging.ThreadAttachment": (),
    "messaging.ThreadFollower.thread -> messaging.Thread": (),
    "messaging.ThreadNotification.attachment -> messaging.ThreadAttachment": (),
    "messaging.ThreadNotification.message -> messaging.Message": (),
    "messaging.ThreadNotification.thread -> messaging.Thread": (),
    "parties.Address.party -> parties.Party": (
        "console OrganizationType.addresses", "console PartyType.addresses", "console PersonType.addresses",
        "public OrganizationType.addresses", "public PartyType.addresses", "public PersonType.addresses",
    ),
    "parties.CircleMember.party -> parties.Party": (
        "console OrganizationType.circle_members", "console PartyType.circle_members",
        "console PersonType.circle_members", "public OrganizationType.circle_members",
        "public PartyType.circle_members", "public PersonType.circle_members",
    ),
    "parties.Directory.integration_ptr -> integrate.Integration": (),
    "parties.Handle.party -> parties.Party": (
        "console OrganizationType.handles", "console PartyType.handles", "console PersonType.handles",
        "public OrganizationType.handles", "public PartyType.handles", "public PersonType.handles",
    ),
    "parties.Organization.party_ptr -> parties.Party": (),
    "parties.Person.party_ptr -> parties.Party": (),
    "parties.Relationship.party -> parties.Party": (
        "console OrganizationType.relationships", "console PartyType.relationships",
        "console PersonType.relationships", "public OrganizationType.relationships", "public PartyType.relationships",
        "public PersonType.relationships",
    ),
    "posts.Feed.channel_ptr -> messaging.Channel": (),
    "posts.PostMetrics.message -> messaging.Message": (),
    "posts.Quota.integration -> integrate.Integration": (),
    "projects.Milestone.project -> projects.Project": (),
    "projects.Participant.project -> projects.Project": (),
    "projects.Project.team -> spaces.Group": (),
    "projects.ProjectBinding.project -> projects.Project": (),
    "proposals.Answer.proposal -> proposals.Proposal": (),
    "proposals.Proposal.round -> proposals.Round": (
        "console ProposalRoundType.roster", "public ProposalRoundType.roster",
    ),
    "proposals.Review.proposal -> proposals.Proposal": (),
    "proposals.Round.team -> spaces.Group": (),
    "proposals.Topic.round -> proposals.Round": (),
    "spaces.Membership.group -> spaces.Group": (),
    "storage.FileAttachment.file -> storage.File": (),
    "storage.Folder.drive -> storage.Drive": (),
    "storage_integrate.Mount.integration_ptr -> integrate.Integration": (),
    "webforms.FormAlias.form -> webforms.Form": (),
    "webforms.FormVersion.form -> webforms.Form": (),
    "webforms.Response.form -> webforms.Form": (),
    "webforms.ResponseRecord.response -> webforms.Response": (),
    "work.Cycle.queue -> work.Queue": (),
    "work.Queue.group_ptr -> spaces.Group": (),
    "work.Stage.queue -> work.Queue": ("console WorkQueueType.stages", "public WorkQueueType.stages"),
    "workflows.StepAttempt.step_run -> workflows.StepRun": ("console StepRunType.attempts",),
    "workflows.StepRecord.run -> workflows.WorkflowRun": ("console WorkflowRunType.records",),
    "workflows.StepRun.run -> workflows.WorkflowRun": ("console WorkflowRunType.step_runs",),
    "workflows.StepWatch.step_run -> workflows.StepRun": ("console StepRunType.watches",),
    "workflows.Trigger.workflow -> workflows.Workflow": (),
    "workflows.TriggerEvent.trigger -> workflows.Trigger": (),
    "workflows.WorkflowVersion.workflow -> workflows.Workflow": (),
}

# The fields the differential compares: every pinned field whose type a root of its schema lists,
# and every resolver of ``RESOLVER_DOCUMENTS``.
DIFFERENTIAL_FIELDS: tuple[str, ...] = (
    "console CalendarSourceType.open_failure_count",
    "console ChannelType.open_failure_count",
    "console DecisionType.records",
    "console DirectoryType.open_failure_count",
    "console ExtractionType.sources",
    "console FeedType.open_failure_count",
    "console MessageType.participants",
    "console MessageType.parts",
    "console MessageType.reaction_groups",
    "console MessageType.starred",
    "console MountType.open_failure_count",
    "console NotionConnectionType.open_failure_count",
    "console OrganizationType.addresses",
    "console OrganizationType.circle_members",
    "console OrganizationType.handles",
    "console OrganizationType.relationships",
    "console PageType.kind",
    "console PageType.markdown",
    "console PageType.wikilinks",
    "console PartyType.addresses",
    "console PartyType.circle_members",
    "console PartyType.handles",
    "console PartyType.relationships",
    "console PersonType.addresses",
    "console PersonType.circle_members",
    "console PersonType.handles",
    "console PersonType.relationships",
    "console ProposalRoundType.roster",
    "console Query.dashboard_summaries",
    "console StepRunType.attempts",
    "console StepRunType.watches",
    "console SyncStreamType.failures",
    "console SyncStreamType.open_failure_count",
    "console ThreadType.participants",
    "console VcsBridgeType.open_failure_count",
    "console WorkQueueType.stages",
    "console WorkflowRunType.records",
    "console WorkflowRunType.step_runs",
    "public DirectoryType.open_failure_count",
    "public OrganizationType.addresses",
    "public OrganizationType.circle_members",
    "public OrganizationType.handles",
    "public OrganizationType.relationships",
    "public PageType.kind",
    "public PageType.markdown",
    "public PageType.wikilinks",
    "public PartyType.addresses",
    "public PartyType.circle_members",
    "public PartyType.handles",
    "public PartyType.relationships",
    "public PersonType.addresses",
    "public PersonType.circle_members",
    "public PersonType.handles",
    "public PersonType.relationships",
    "public ProposalRoundType.roster",
    "public WorkQueueType.stages",
)

# A resolver's own prefetch over an edge, which the schema does not show: edge -> the fields that run it.
# Every prefetch in the framework's source over a pinned edge's relation was read: the others are a
# field's own hint (``edge_fields`` finds those), a standalone ``prefetch_related_objects`` (it opens no
# admission), a system queryset (no permission statement either way), or a read of an edge a field
# already carries.
RESOLVER_PREFETCHES: dict[str, tuple[str, ...]] = {
    "dashboards.DashboardWidget.dashboard -> dashboards.Dashboard": ("console Query.dashboard_summaries",),
}

# The pinned resolvers the differential reads: field -> (document, the path to the rows, the key that
# holds what the row's children make).
RESOLVER_DOCUMENTS: dict[str, tuple[str, tuple[str, ...], str]] = {
    "console Query.dashboard_summaries": (
        "query { dashboard_summaries { items { id resources presets } } }",
        ("dashboard_summaries", "items"),
        "resources",
    ),
}

# The edges a pinned field reads that the differential sees delivered with the switch on, for every actor.
GRAPHQL_DELIVERED: tuple[str, ...] = (
    "dashboards.DashboardWidget.dashboard -> dashboards.Dashboard",
    "decisions.DecisionRecord.decision -> decisions.Decision",
    "extraction.ExtractionSource.extraction -> extraction.Extraction",
    "integrate.RecordLink.stream -> integrate.SyncStream",
    "knowledge.Link.source_page -> knowledge.Page",
    "knowledge.MarkdownPage.page_ptr -> knowledge.Page",
    "messaging.MessageStar.message -> messaging.Message",
    "messaging.Part.message -> messaging.Message",
    "messaging.Participant.message -> messaging.Message",
    "messaging.Participant.thread -> messaging.Thread",
    "messaging.Reaction.message -> messaging.Message",
    "work.Stage.queue -> work.Queue",
    "workflows.StepAttempt.step_run -> workflows.StepRun",
    "workflows.StepRecord.run -> workflows.WorkflowRun",
    "workflows.StepRun.run -> workflows.WorkflowRun",
    "workflows.StepWatch.step_run -> workflows.StepRun",
)

_PARTY_CHILD = (
    "the arm is party->role_read, which a party admitted under read does not hold, and the rows are read "
    "below a person or an organization while the key names the party"
)

# The edges a pinned field reads that GraphQL never delivers, and why.
NOT_DELIVERED: dict[str, str] = {
    "integrate.SyncStream.integration -> integrate.Integration": (
        "every field that reads it is on a bridge subtype, whose rows are admitted as that subtype "
        "while the key names the integration"
    ),
    "parties.Address.party -> parties.Party": _PARTY_CHILD,
    "parties.CircleMember.party -> parties.Party": _PARTY_CHILD,
    "parties.Handle.party -> parties.Party": _PARTY_CHILD,
    "parties.Relationship.party -> parties.Party": _PARTY_CHILD,
    "proposals.Proposal.round -> proposals.Round": (
        "the roster reads a round's proposals through Proposal.system_queryset(), with no permission "
        "statement either way"
    ),
}

_NO_BRIDGE = "no bridge of this kind is seeded; ChannelType.open_failure_count reads the same edges"
_NO_ROUND = "no round is seeded: the roster reads its proposals by a system queryset (NOT_DELIVERED)"

# The compared fields that read no parent for any actor, and why.
EMPTY_FIELDS: dict[str, str] = {
    "console CalendarSourceType.open_failure_count": _NO_BRIDGE,
    "console DirectoryType.open_failure_count": _NO_BRIDGE,
    "console FeedType.open_failure_count": _NO_BRIDGE,
    "console MountType.open_failure_count": _NO_BRIDGE,
    "console NotionConnectionType.open_failure_count": _NO_BRIDGE,
    "console ProposalRoundType.roster": _NO_ROUND,
    "console VcsBridgeType.open_failure_count": _NO_BRIDGE,
    "public DirectoryType.open_failure_count": _NO_BRIDGE,
    "public ProposalRoundType.roster": _NO_ROUND,
}

# Edges REBAC refuses because a child can be withheld from its parent's reader, each with a field that
# reads it: the seed gives the narrow actor such a parent and such a child.
WITHHELD: dict[str, str] = {
    # A trashed message is withheld from its thread's readers (``read`` excludes ``withheld``).
    "messaging.Message.thread -> messaging.Thread": "console ThreadType.messages",
    # An extraction part also needs read on its source's content (``read`` is an intersection).
    "extraction.ExtractionPart.extraction -> extraction.Extraction": "console ExtractionType.parts",
    # A relationship naming a party as its other side is not read through that party.
    "parties.Relationship.other_party -> parties.Party": "console PersonType.inbound_relationships",
}

# Display fields: a referred record's label, shown with the row that refers to it to whoever reads that row.
# The models that declare them (Meta.rebac_display_fields), with the fields they declare.
DISPLAY_FIELDS: dict[str, tuple[str, ...]] = {
    "messaging.Thread": ("modality",),
    "projects.Milestone": ("name",),
}
# model -> the GraphQL fields that can show its display fields: "schema Type.field" -> (holder, kind).
DISPLAY_AUDIENCE: dict[str, dict[str, tuple[str, str]]] = {
    "messaging.Thread": {
        "console MessageType.thread": ("messaging.Message", "prefetch"),
        "console ThreadAttachmentType.thread": ("messaging.ThreadAttachment", "prefetch"),
        "console ThreadFollowerType.thread": ("messaging.ThreadFollower", "prefetch"),
        "console ThreadNotificationType.thread": ("messaging.ThreadNotification", "prefetch"),
        "public MessageType.thread": ("messaging.Message", "prefetch"),
    },
    "projects.Milestone": {
        "console ConsoleProjectType.current_milestone": ("projects.Project", "prefetch"),
        "console ConsoleTaskType.milestone": ("projects.Task", "prefetch"),
        "console ProjectType.current_milestone": ("projects.Project", "prefetch"),
        "console ProposalRoundType.opens_after": ("proposals.Round", "prefetch"),
        "console ProposalRoundType.clarifications_shared_until": ("proposals.Round", "prefetch"),
        "console TaskType.milestone": ("projects.Task", "prefetch"),
        "public ProjectType.current_milestone": ("projects.Project", "prefetch"),
        "public ProposalRoundType.opens_after": ("proposals.Round", "prefetch"),
        "public ProposalRoundType.clarifications_shared_until": ("proposals.Round", "prefetch"),
        "public TaskType.milestone": ("projects.Task", "prefetch"),
    },
}
# holder -> who reads it: the arms of its read in the composed declaration (read_arms).
HOLDER_READERS: dict[str, tuple[str, ...]] = {
    "messaging.Message": (
        "thread->read", "channel->read", "editor", "reader", "channel->write",
        "identity_role->effective_member", "admin->member",
    ),
    "messaging.ThreadAttachment": (
        "thread->read", "editor", "reader", "admin->member",
    ),
    "messaging.ThreadFollower": (
        "user", "thread->read", "attachment->read", "admin->member",
    ),
    "messaging.ThreadNotification": (
        "user", "thread->read", "attachment->read", "message->read", "admin->member",
    ),
    "projects.Project": (
        "owner", "reader", "editor", "admin->member", "source_proposal->read_track", "round_responder",
        "team->post",
    ),
    "projects.Task": (
        "inherited_project->read", "owner", "assignee", "reader", "editor", "requester", "round_responder",
        "shared_clarification_round->respond", "clarification_asker", "responder_shared_round->respond",
        "track_proposal->manage", "clarification_round->manage", "inherited_queue->read", "unbound->free",
        "subject_product->read", "subject_initiative->read", "subject_task->read_as_subject",
        "subject_project->read", "subject_party->read_as_subject", "subject_page->read_as_subject",
        "subject_vault->read_as_subject", "subject_round->manage", "subject_note->read",
        "inherited_project->write", "inherited_queue->write", "trashed->conceal",
        "subject_task->keeper_as_subject", "admin->member",
    ),
    "proposals.Round": (
        "facilitator", "team->write", "person->active_member", "admin->member", "team->post", "responder",
        "reader", "evaluator", "requester", "proposal_viewer", "project->proposal_viewer",
        "task->proposal_viewer", "task_project->proposal_viewer", "global_viewer->effective_member",
    ),
}

# (field, actor): the differential's rows whose label shows where the actor may not read the record. The
# narrow actor reads one of the member's messages and not its thread; every other reader reads both.
LABEL_SHOWN = {("console MessageType.thread", "narrow")}


def _edge(fk: models.ForeignKey[Any, Any]) -> str:
    return f"{fk.model._meta.label}.{fk.name} -> {fk.related_model._meta.label}"


def _child_table(edge: str) -> str:
    app_label, model_name, _fk = edge.split(" -> ")[0].split(".")
    return apps.get_model(app_label, model_name)._meta.db_table


def delivered_edges() -> dict[str, tuple[str, ...]]:
    """Every edge REBAC delivers for the composed policy, with the parent permissions that deliver it.

    REBAC lists them itself (``rebac.structural.edges``), each child read under its read action:
    every foreign key and one-to-one field, parent links included, to another protected model's
    primary key, with every parent permission under which the child's read is proven.
    """

    listed = structural.edges()
    assert {edge.child_action for edge in listed} == {"read"}, "a child is read under another action than read"
    return dict(sorted(
        (f"{edge.child_model._meta.label}.{edge.fk_name} -> {edge.parent_model._meta.label}", edge.parent_actions)
        for edge in listed
    ))


@contextmanager
def observed_deliveries() -> Iterator[list[str]]:
    """The edges REBAC delivers inside the block, one entry per child statement it read without its own.

    REBAC delivers a child statement exactly when its proof admits the edge for parents the running
    prefetch admitted, so its verdicts are observed where it asks for them.
    """

    seen: list[str] = []
    proof = structural.parent_implies_children

    def observing(policy: Any, **edge: Any) -> bool:
        proven = bool(proof(policy, **edge))
        if proven:
            seen.append(f"{edge['child_model']._meta.label}.{edge['fk_name']} -> {edge['parent_model']._meta.label}")
        return proven

    with patch.object(structural, "parent_implies_children", observing):
        yield seen


def assert_read_by_pinned_fields(seen: list[str]) -> None:
    """Fail when REBAC delivered along an edge that no pinned field reads, so no differential compares it."""

    unread = sorted({edge for edge in seen if not EDGE_FIELDS.get(edge)})
    assert not unread, (
        f"REBAC delivered children along {unread} with no pinned field reading them: pin the field that "
        "reads them in tests.native_read_through_parent (EDGE_FIELDS, or RESOLVER_PREFETCHES for a "
        "resolver's own prefetch) so the differential compares it"
    )


@contextmanager
def observed_labels() -> Iterator[list[str]]:
    """The models whose rows REBAC was asked to read as display rows inside the block.

    REBAC's public request (``RebacQuerySet.for_display``) is observed where the relation
    fields make it: they ask exactly when every reader of the relation in the document reads
    its target's display fields alone. Which fields can ask is ``DISPLAY_AUDIENCE``'s.
    """

    seen: list[str] = []
    request = RebacQuerySet.for_display

    def observing(queryset: Any) -> Any:
        seen.append(queryset.model._meta.label)
        return request(queryset)

    with patch.object(RebacQuerySet, "for_display", observing):
        yield seen


def assert_labels_through_pinned_fields(seen: list[str]) -> None:
    """Fail when display rows were asked for a model whose display fields ``DISPLAY_AUDIENCE`` does not pin."""

    unpinned = sorted(set(seen) - set(DISPLAY_AUDIENCE))
    assert not unpinned, (
        f"display rows of {unpinned} were asked for without a pin: list the model and every field that can "
        "show its display fields in DISPLAY_AUDIENCE (tests.native_read_through_parent)"
    )


def display_audience_pins() -> tuple[dict[str, tuple[str, ...]], dict[str, dict[str, tuple[str, str]]], dict[str, Any]]:
    """The declared display fields, every field that can show them and the readers of each holder, as pinned."""

    schemas = GraphQLSchemas.from_discovery()
    declared = {
        model._meta.label: tuple(fields)
        for model in apps.get_models()
        if (fields := getattr(model._meta, "rebac_display_fields", None))
    }
    audience: dict[str, dict[str, tuple[str, str]]] = {}
    readers: dict[str, Any] = {}
    for label in declared:
        for entry in relations.display_audience(apps.get_model(label), schemas):
            spelled = f"{entry.schema} {entry.type_name}.{entry.field}"
            audience.setdefault(label, {})[spelled] = (entry.holder, entry.kind)
            readers[entry.holder] = entry.readers
    return declared, audience, readers


def _graphql_model(graphql_type: Any) -> type[models.Model] | None:
    definition = (getattr(graphql_type, "extensions", None) or {}).get("strawberry-definition")
    django = getattr(getattr(definition, "origin", None), "__strawberry_django_definition__", None)
    return getattr(django, "model", None)


def _reverse_paths(source: Any) -> set[str]:
    """The first segments of the relations a GraphQL field reads, by name or by a declared prefetch.

    A prefetch hint given as a function of the request is asked without one, as a field read
    outside a request would; one that needs the request names no relation here.
    """

    paths = {str(getattr(source, "django_name", "") or "")}
    store = getattr(source, "store", None)
    for hint in getattr(store, "prefetch_related", None) or ():
        if callable(hint) and not isinstance(hint, models.Prefetch):
            try:
                hint = hint(None)
            except Exception:  # noqa: BLE001 - a hint that needs the request is not resolved here
                continue
        lookup = hint if isinstance(hint, str) else getattr(hint, "prefetch_through", None)
        if isinstance(lookup, str):
            paths.add(lookup.split("__", 1)[0])
    return paths - {""}


def edge_fields(schemas: GraphQLSchemas, edges: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    """For each edge, the fields of every named schema that read its children from a parent row.

    The object-type fields the schema shows, and the resolvers ``RESOLVER_PREFETCHES`` names.
    """

    found: dict[str, set[str]] = {edge: set(RESOLVER_PREFETCHES.get(edge, ())) for edge in edges}
    for name in schemas.names():
        core = schemas.graphql_schema(name)
        for type_name, graphql_type in sorted(core.type_map.items()):
            model = _graphql_model(graphql_type)
            if model is None or not isinstance(graphql_type, GraphQLObjectType):
                continue
            for wire, graphql_field in graphql_type.fields.items():
                source = (graphql_field.extensions or {}).get("strawberry-definition")
                for path in _reverse_paths(source):
                    try:
                        relation = model._meta.get_field(path)
                    except Exception:  # noqa: BLE001 - a field over no model relation reads no edge
                        continue
                    # A reverse foreign key or one-to-one: the children are read by the key naming the row.
                    if isinstance(relation, models.ManyToOneRel):
                        edge = _edge(relation.field)
                        if edge in found:
                            found[edge].add(f"{name} {type_name}.{wire}")
    return {edge: tuple(sorted(fields)) for edge, fields in found.items()}


def _named(graphql_type: Any) -> Any:
    while isinstance(graphql_type, GraphQLNonNull | GraphQLList):
        graphql_type = graphql_type.of_type
    return graphql_type


def _root(core: Any, type_name: str) -> str | None:
    """The shortest root field that lists ``type_name`` and needs no argument, if there is one."""

    found = [
        wire for wire, graphql_field in core.query_type.fields.items()
        if isinstance(graphql_field.type, GraphQLNonNull) and isinstance(graphql_field.type.of_type, GraphQLList)
        and getattr(_named(graphql_field.type), "name", None) == type_name
        and not any(isinstance(argument.type, GraphQLNonNull) for argument in graphql_field.args.values())
    ]
    return min(found, key=lambda wire: (len(wire), wire)) if found else None


def _selection(graphql_field: Any) -> str:
    """The selection a field's own name takes: its type's scalar and enum leaves when it returns objects."""

    named = _named(graphql_field.type)
    return f"{{ {' '.join(_leaves(named))} }}" if isinstance(named, GraphQLObjectType) else ""


def _leaves(graphql_type: GraphQLObjectType) -> list[str]:
    """The scalar and enum fields of a type that take no required argument."""

    return [
        wire for wire, graphql_field in graphql_type.fields.items()
        if isinstance(_named(graphql_field.type), GraphQLScalarType | GraphQLEnumType)
        and not any(isinstance(argument.type, GraphQLNonNull) for argument in graphql_field.args.values())
    ]


Document = tuple[str, str, str, tuple[str, ...], str]
# A document read with the switch off and on, and the edges delivered in each.
Reads = tuple[dict[str, Any], dict[str, Any], list[str], list[str]]


def _documents(schemas: GraphQLSchemas, fields: tuple[str, ...]) -> Iterator[Document]:
    """Each field with its schema, a document reading parents with it, the path to the parents and its key."""

    for spelled in fields:
        schema_name, path = spelled.split(" ")
        if spelled in RESOLVER_DOCUMENTS:
            document, rows, key = RESOLVER_DOCUMENTS[spelled]
            yield spelled, schema_name, document, rows, key
            continue
        type_name, field_name = path.split(".")
        core = schemas.graphql_schema(schema_name)
        root = _root(core, type_name)
        selection = _selection(core.type_map[type_name].fields[field_name])
        yield spelled, schema_name, f"query {{ {root} {{ id {field_name} {selection} }} }}", (str(root),), field_name


def _compared(read: dict[str, Any], path: tuple[str, ...], key: str) -> dict[str, Any]:
    """What a comparison holds: each parent's id with what the field read for it, and the errors."""

    rows: Any = read["data"]
    for step in path:
        rows = (rows or {}).get(step)
    return {"errors": read["errors"], "rows": [(row["id"], row[key]) for row in rows or []]}


def _children(value: Any) -> int:
    """How many children a compared value shows: a list's length; a truthy scalar (a count, a flag) as two."""

    return len(value) if isinstance(value, list) else 2 * bool(value)


def _signed_in(window: int) -> Any:
    """Sign every download URL of one comparison in the same token window."""

    return patch.object(DownloadTokenSigner, "timestamp", return_value=window)


class ReadThroughParentEdgeTests(TransactionTestCase):
    """The composed policy's deliverable edges, and what each field that reads one returns with the switch."""

    def setUp(self):
        call_command("rebac", "sync", verbosity=0)
        call_command("resources", "load", verbosity=0)
        users = get_user_model().objects
        self.admin, _created = users.ensure_bootstrap_admin(
            "edges-admin", email="edges-admin@example.test", password="edges-admin",
        )
        self.member = users.create_person_as_system(
            username="edges-member", email="edges-member@example.test", reason="tests.read_through_parent.member",
        )
        self.narrow = users.create_person_as_system(
            username="edges-narrow", email="edges-narrow@example.test", reason="tests.read_through_parent.narrow",
        )
        self.actors = {"admin": self.admin, "member": self.member, "narrow": self.narrow}
        self.window = DownloadTokenSigner().timestamp()
        self.withheld: dict[str, set[str]] = {}

    def test_the_composed_policy_delivers_the_pinned_edges(self):
        self.assertEqual(delivered_edges(), DELIVERED_EDGES)
        # A recursion's edge stays read by its statement: below a parent admitted at the depth limit
        # it would deliver subfolders the statement withholds.
        self.assertNotIn("storage.Folder.parent -> storage.Folder", DELIVERED_EDGES)

    def test_the_pinned_edges_are_read_by_the_pinned_graphql_fields(self):
        self.assertEqual(edge_fields(GraphQLSchemas.from_discovery(), DELIVERED_EDGES), EDGE_FIELDS)
        read = {edge for edge, fields in EDGE_FIELDS.items() if fields}
        self.assertEqual(read, set(GRAPHQL_DELIVERED) | set(NOT_DELIVERED))
        self.assertFalse(set(GRAPHQL_DELIVERED) & set(NOT_DELIVERED))
        self.assertFalse(set(WITHHELD) & set(DELIVERED_EDGES))

    def test_the_differential_reads_every_field_a_root_lists(self):
        schemas = GraphQLSchemas.from_discovery()
        listed = sorted(
            spelled for fields in EDGE_FIELDS.values() for spelled in fields
            if spelled in RESOLVER_DOCUMENTS
            or _root(schemas.graphql_schema(spelled.split(" ")[0]), spelled.split(" ")[1].split(".")[0])
        )
        self.assertEqual(listed, sorted(DIFFERENTIAL_FIELDS))

    def test_every_field_that_reads_an_edge_reads_the_same_children_with_the_switch(self):
        write_relationships(self.seed())
        schemas = GraphQLSchemas.from_discovery()
        # field -> actor -> (parents read, the most children one parent showed)
        read: dict[str, dict[str, tuple[int, int]]] = {}
        # actor -> edges delivered with the switch on; (field, actor) -> statements off and on, edges delivered
        delivered: dict[str, set[str]] = {role: set() for role in self.actors}
        statements: dict[tuple[str, str], tuple[list[str], list[str], set[str]]] = {}
        for spelled, schema_name, document, path, key in _documents(schemas, DIFFERENTIAL_FIELDS):
            schema = schemas.build(schema_name)
            for role, actor in self.actors.items():
                with self.subTest(field=spelled, actor=role):
                    off, on, seen_off, seen_on = self.read_both(schema, document, actor)
                    compared = [_compared(result, path, key) for result in (off, on)]
                    values = [value for _id, value in compared[0]["rows"]]
                    read.setdefault(spelled, {})[role] = (len(values), max(map(_children, values), default=0))
                    delivered[role].update(seen_on)
                    statements[spelled, role] = (off["sql"], on["sql"], set(seen_on))
                    self.assertEqual(compared[1], compared[0])
                    self.assertEqual(compared[0]["errors"], [])
                    self.assertEqual(seen_off, [])
        self.assertEqual(set(read), set(DIFFERENTIAL_FIELDS))
        # Each pinned edge delivered to every actor with the switch on, none with it off (asserted per
        # read above): REBAC read the children without their permission statement, which shows in the
        # statements of the child's table changing with the switch.
        for role in self.actors:
            with self.subTest(delivered_to=role):
                self.assertEqual(sorted(delivered[role]), sorted(GRAPHQL_DELIVERED))
        for edge in GRAPHQL_DELIVERED:
            table = f'"{_child_table(edge)}"'
            for role in self.actors:
                with self.subTest(edge=edge, actor=role):
                    runs = [
                        (off, on)
                        for (_field, who), (off, on, seen) in statements.items()
                        if who == role and edge in seen
                    ]
                    self.assertTrue(runs)
                    for off_sql, on_sql in runs:
                        self.assertNotEqual(
                            [sql for sql in on_sql if table in sql], [sql for sql in off_sql if table in sql],
                        )
        # No comparison passed over nothing unnamed: a field no actor read a parent through is named,
        # with why, and every other edge was read with children.
        empty = sorted(
            spelled for spelled, counts in read.items() if not any(parents for parents, _ in counts.values())
        )
        self.assertEqual(empty, sorted(EMPTY_FIELDS))
        for edge, fields in EDGE_FIELDS.items():
            seen = [read[spelled] for spelled in fields if spelled in read and spelled not in EMPTY_FIELDS]
            if not seen:
                continue
            with self.subTest(edge=edge):
                self.assertGreaterEqual(max(counts["admin"][1] for counts in seen), 2, seen)
                self.assertGreaterEqual(max(counts["member"][1] for counts in seen), 2, seen)
                self.assertGreaterEqual(max(counts["narrow"][0] for counts in seen), 1, seen)
        # The narrow actor is narrow: no administrator, and fewer messages and threads than their owner.
        self.assertFalse(is_platform_admin(self.narrow))
        for spelled in ("console MessageType.parts", "console ThreadType.participants"):
            with self.subTest(narrow=spelled):
                self.assertGreater(read[spelled]["narrow"][0], 0)
                self.assertLess(read[spelled]["narrow"][0], read[spelled]["member"][0])

    def test_a_child_withheld_from_its_parents_reader_stays_withheld_with_the_switch(self):
        """Along an edge REBAC refuses, a child the parent's reader may not read stays withheld, by its statement."""

        write_relationships(self.seed())
        schemas = GraphQLSchemas.from_discovery()
        self.assertEqual(set(self.withheld), set(WITHHELD))
        for edge, spelled in WITHHELD.items():
            schema_name, path = spelled.split(" ")
            type_name, field_name = path.split(".")
            root = _root(schemas.graphql_schema(schema_name), type_name)
            document = f"query {{ {root} {{ id {field_name} {{ id }} }} }}"
            schema = schemas.build(schema_name)
            for role, actor in self.actors.items():
                with self.subTest(edge=edge, actor=role):
                    off, on, _seen_off, seen_on = self.read_both(schema, document, actor)
                    compared = [_compared(result, (str(root),), field_name) for result in (off, on)]
                    self.assertEqual(compared[1], compared[0])
                    self.assertEqual(compared[0]["errors"], [])
                    self.assertNotIn(edge, seen_on)
                    # Nothing was read without its statement: the switch changed no statement.
                    self.assertEqual(on["sql"], off["sql"])
                    children = {child["id"] for _id, rows in compared[1]["rows"] for child in rows}
                    if role == "narrow":
                        self.assertTrue(compared[1]["rows"])
                        self.assertFalse(self.withheld[edge] & children)
                    else:
                        self.assertLessEqual(self.withheld[edge], children)

    def test_a_stored_override_stops_every_delivery(self):
        budgets.ConsoleDocumentBudgetTests.seed(self, self.member, "edges")
        schemas = GraphQLSchemas.from_discovery()
        fields = ("console MessageType.parts", "console MessageType.participants", "console ThreadType.participants")
        documents = list(_documents(schemas, fields))
        schema = schemas.build("console")
        for _spelled, _schema, document, _path, _key in documents:
            _off, _on, _seen_off, seen_on = self.read_both(schema, document, self.member)
            self.assertTrue(seen_on, document)
        from rebac.models import SchemaOverride, SchemaPermission

        read = SchemaPermission.objects.get(definition__resource_type="messaging/part", name="read")
        # Tightened by what it already requires: the same readers, now under an override.
        SchemaOverride.objects.create(
            kind=SchemaOverride.KIND_TIGHTEN, target=read, expression="message->read",
            reason="tests.read_through_parent",
        )
        for spelled, _schema, document, path, key in documents:
            with self.subTest(field=spelled):
                off, on, _seen_off, seen_on = self.read_both(schema, document, self.member)
                self.assertEqual(seen_on, [])
                self.assertEqual(_compared(on, path, key), _compared(off, path, key))
                self.assertTrue(any(_children(value) for _id, value in _compared(off, path, key)["rows"]))

    def read_both(self, schema: Any, document: str, actor: Any) -> Reads:
        """The document read with the switch off and on, each after a warm read, and the edges delivered in each."""

        with _signed_in(self.window):
            with observed_deliveries() as seen_off:
                off = self.execute(schema, document, actor)
            with (
                override_settings(**READ_THROUGH_PARENT),
                observed_deliveries() as seen_on,
                observed_labels() as labels,
            ):
                on = self.execute(schema, document, actor)
        assert_read_by_pinned_fields(seen_on)
        assert_labels_through_pinned_fields(labels)
        return off, on, seen_off, seen_on

    def execute(self, schema: Any, document: str, actor: Any) -> dict[str, Any]:
        from django.db import connection

        execute_schema(schema, document, {}, user=actor)
        with CaptureQueriesContext(connection) as captured:
            result = execute_schema(schema, document, {}, user=actor)
        return {
            **json.loads(json.dumps(
                {"data": result.data, "errors": sorted(str(error) for error in result.errors or [])}, default=str,
            )),
            "sql": [query["sql"] for query in captured.captured_queries],
        }

    def seed(self) -> list[RelationshipTuple]:
        """Give the member parents with two children or more along every read edge, the narrow actor one parent
        of each kind alone, and record in withheld the children of WITHHELD the narrow actor may not read."""

        # Through the module: a test class imported here would run again under this module's label.
        ids = budgets.ConsoleDocumentBudgetTests.seed(self, self.member, "edges")
        return [
            *self.seed_messages(ids),
            *self.seed_parties(ids),
            *self.seed_queues(),
            *self.seed_decisions(),
            *self.seed_workflow_runs(),
            *self.seed_extractions(),
            *self.seed_pages(),
            *self.seed_sync_failures(),
            *self.seed_dashboards(),
        ]

    def seed_messages(self, ids: dict[str, str]) -> list[RelationshipTuple]:
        Message = apps.get_model("messaging", "Message")
        MessageStar = apps.get_model("messaging", "MessageStar")
        Thread = apps.get_model("messaging", "Thread")
        with system_context(reason="tests.read_through_parent.messages"):
            message = Message.objects.get(sqid=ids["message"])
            thread = Thread.objects.get(sqid=ids["thread"])
            messages = list(Message.objects.filter(channel__owner=self.member).order_by("sent_at"))
            # The narrow actor reads this thread; a trashed message in it stays its channel's alone.
            trashed = Message.objects.filter(thread=thread).exclude(pk=message.pk).order_by("sent_at").first()
        for user in (self.member, self.admin):
            with actor_context(user):
                for row in messages:
                    # The budget seed's ``+1`` on the last three makes them two reaction groups.
                    Message.objects.set_reaction(row, reaction="heart", action="add", user=user)
                    MessageStar.objects.set_starred(row, user=user, starred=True)
        with system_context(reason="tests.read_through_parent.trash"):
            trashed.trash(reason="tests.read_through_parent")
        self.withheld["messaging.Message.thread -> messaging.Thread"] = {public_id_of(trashed)}
        narrow = to_subject_ref(self.narrow)
        return [
            RelationshipTuple(to_object_ref(message), "reader", narrow),
            RelationshipTuple(to_object_ref(thread), "reader", narrow),
        ]

    def seed_parties(self, ids: dict[str, str]) -> list[RelationshipTuple]:
        Handle = apps.get_model("parties", "Handle")
        Address = apps.get_model("parties", "Address")
        Circle = apps.get_model("parties", "Circle")
        CircleMember = apps.get_model("parties", "CircleMember")
        Relationship = apps.get_model("parties", "Relationship")
        RelationshipKind = apps.get_model("parties", "RelationshipKind")
        Person = apps.get_model("parties", "Person")
        Organization = apps.get_model("parties", "Organization")
        with system_context(reason="tests.read_through_parent.parties"):
            person = Person.objects.get(sqid=ids["person"])
            kinds = list(RelationshipKind.objects.order_by("pk")[:2])
        with actor_context(self.member):
            handle = Handle.objects.upsert(platform="email", value="ada.second@example.test")
            person.with_actor(self.member).confirm_statement(aspect="handle", handle=handle, values={})
            Address.objects.create(party=person, label="work", street="1 Bank Street", city="London", country="GB")
            other = Person.objects.create(display_name="Babbage edges", given_name="Charles")
            for index, kind in enumerate(kinds):
                Relationship.objects.create(party=person, other_party=other, kind=kind, title=f"Role {index}")
            # Naming the person as its other side: read through the other party, never through the person.
            inbound = Relationship.objects.create(party=other, other_party=person, kind=kinds[0], title="Inbound")
            organization = Organization.objects.create(display_name="Edges organization")
            for index, kind in enumerate(kinds):
                Address.objects.create(party=organization, label=f"site {index}", city="London", country="GB")
                Relationship.objects.create(party=organization, other_party=other, kind=kind, title=f"Role {index}")
            for index in range(2):
                circle = Circle.objects.create(name=f"Edges circle {index}")
                CircleMember.objects.create(circle=circle, party=person)
                CircleMember.objects.create(circle=circle, party=organization)
        self.withheld["parties.Relationship.other_party -> parties.Party"] = {public_id_of(inbound)}
        return [RelationshipTuple(to_object_ref(person), "reader", to_subject_ref(self.narrow))]

    def seed_queues(self) -> list[RelationshipTuple]:
        Queue = apps.get_model("work", "Queue")
        Membership = apps.get_model("spaces", "Membership")
        Person = apps.get_model("parties", "Person")
        with system_context(reason="tests.read_through_parent.queues"):
            queues = [
                Queue.objects.create(name=f"Edges {index}", key=f"EDG{index}", owner=self.member) for index in range(2)
            ]
            Membership.objects.create(
                group=queues[0], party=Person.objects.get(user=self.narrow), role="viewer", is_confirmed=True,
            )
        return []

    def seed_decisions(self) -> list[RelationshipTuple]:
        from angee.decisions.contracts import DecisionProposal, DecisionRequest

        Decision = apps.get_model("decisions", "Decision")
        Project = apps.get_model("projects", "Project")
        with system_context(reason="tests.read_through_parent.decision_records"):
            projects = list(Project.objects.filter(lead=self.member).order_by("pk"))
        asked = [
            Decision.objects.ask(
                DecisionRequest(
                    kind="edges_review",
                    records=tuple(projects[index:index + 2]),
                    assignees=(self.member,),
                    proposal=DecisionProposal(
                        alternatives=[{"key": "confirm", "label": "Confirm", "outcome": "confirmed"}],
                    ),
                ),
                actor=self.member,
            )
            for index in range(2)
        ]
        with system_context(reason="tests.read_through_parent.decision_grant"):
            Decision.objects.get(pk=asked[0].pk).assignees.add(self.narrow)
        return []

    def seed_workflow_runs(self) -> list[RelationshipTuple]:
        Workflow = apps.get_model("workflows", "Workflow")
        WorkflowRun = apps.get_model("workflows", "WorkflowRun")
        StepRun = apps.get_model("workflows", "StepRun")
        StepRecord = apps.get_model("workflows", "StepRecord")
        StepAttempt = apps.get_model("workflows", "StepAttempt")
        StepWatch = apps.get_model("workflows", "StepWatch")
        Project = apps.get_model("projects", "Project")
        runs = []
        with system_context(reason="tests.read_through_parent.workflow_runs"):
            version = Workflow.objects.filter(published__isnull=False).order_by("pk").first().published
            nodes = list(version.definition.nodes)[:2]
            project = Project.objects.filter(lead=self.member).order_by("pk").first()
            subject = ContentType.objects.get_for_model(Project)
            for index in range(2):
                run = WorkflowRun.objects.create(
                    version=version, run_as=self.member, origin="manual", request_key=f"edges-{index}",
                )
                runs.append(run)
                for rank, node in enumerate(nodes):
                    step = StepRun.objects.create(run=run, node_key=node, rank=rank)
                    StepRecord.objects.create(run=run, step_run=step, content_type=subject, object_id=project.pk)
                    for number in range(2):
                        StepAttempt.objects.create(step_run=step, number=number + 1)
                        StepWatch.objects.create(step_run=step, content_type=subject, object_id=project.pk + number)
        return [RelationshipTuple(to_object_ref(runs[0]), "reader", to_subject_ref(self.narrow))]

    def seed_extractions(self) -> list[RelationshipTuple]:
        from angee.extraction.contracts import DocumentPart, ExtractionPartKind, Result, Source

        Extraction = apps.get_model("extraction", "Extraction")
        Part = apps.get_model("messaging", "Part")
        schema = {
            "$id": "urn:angee:extraction:plain_text:v1", "type": "object", "required": ["text"],
            "properties": {"text": {"type": "string"}},
        }
        extractions = []
        with system_context(reason="tests.read_through_parent.extraction_sources"):
            # A conversation's first message carries a text part and an attachment: two sources.
            attachments = list(
                Part.objects.filter(message__channel__owner=self.member, file__isnull=False)
                .select_related("message", "file").order_by("pk")[:2]
            )
            texts = [
                Part.objects.filter(message=part.message, fragment__isnull=False)
                .select_related("fragment").order_by("pk").first()
                for part in attachments
            ]
            contents = [part.file.read_verified(max_bytes=1 << 16) for part in attachments]
        for index, (attachment, text, content) in enumerate(zip(attachments, texts, contents, strict=True)):
            sources = (
                Source(0, str(text.fragment.hash), "text/plain", text.fragment.text, message_part=text),
                Source(1, str(attachment.file.content_hash), "application/pdf", content, file=attachment.file),
            )
            parts = tuple(
                DocumentPart(
                    position, None, "text/plain", ExtractionPartKind.NATIVE_TEXT, f"Edges {index}.{position}",
                    "native", source.content_hash,
                )
                for position, source in enumerate(sources)
            )
            with actor_context(self.member):
                extractions.append(Extraction.objects.retain_result(
                    sources=sources, result=Result({"text": f"Edges {index}"}, parts, {}), target=attachment.message,
                    actor=self.member, profile="plain_text", schema=schema, request_key=f"edges-{index}",
                ))
        # The narrow actor may read the first extraction but none of its sources' content.
        ExtractionPart = apps.get_model("extraction", "ExtractionPart")
        with system_context(reason="tests.read_through_parent.extraction_parts"):
            self.withheld["extraction.ExtractionPart.extraction -> extraction.Extraction"] = {
                public_id_of(part) for part in ExtractionPart.objects.filter(extraction=extractions[0])
            }
        return [RelationshipTuple(to_object_ref(extractions[0]), "viewer", to_subject_ref(self.narrow))]

    def seed_pages(self) -> list[RelationshipTuple]:
        Vault = apps.get_model("knowledge", "Vault")
        Page = apps.get_model("knowledge", "Page")
        MarkdownPage = apps.get_model("knowledge", "MarkdownPage")
        with actor_context(self.member):
            vault = Vault.objects.create_for(self.member, name="Edges vault")
            pages = [Page.objects.create_in(vault, title=title) for title in ("Alpha", "Beta", "Gamma")]
            for page in pages:
                MarkdownPage.objects.write_body(page, "See [[Alpha]], [[Beta]] and [[Gamma]].")
        return [RelationshipTuple(to_object_ref(pages[0]), "viewer", to_subject_ref(self.narrow))]

    def seed_sync_failures(self) -> list[RelationshipTuple]:
        Vendor = apps.get_model("integrate", "Vendor")
        Channel = apps.get_model("messaging", "Channel")
        Integration = apps.get_model("integrate", "Integration")
        SyncStream = apps.get_model("integrate", "SyncStream")
        RecordLink = apps.get_model("integrate", "RecordLink")
        at = budgets.T0.isoformat()
        failure = {"kind": "semantic", "code": "edges", "created_at": at, "updated_at": at}
        with system_context(reason="tests.read_through_parent.sync_failures"):
            # A channel of its own, with no message: the narrow actor's grant on it reads no conversation.
            channel = Channel.objects.create(
                vendor=Vendor.objects.create(slug="edges-sync", display_name="edges sync"), owner=self.member,
                backend_class="manual", lifecycle="connected", display_name="edges sync",
            )
            streams = [
                SyncStream.objects.create(
                    integration=channel, key=f"edges-{index}", kind="record_replica", direction="pull",
                )
                for index in range(2)
            ]
            for stream in streams:
                for index in range(2):
                    RecordLink.objects.create(
                        stream=stream, external_key=f"edges-{index}", outbox_state="error", outbox_last_error=failure,
                    )
            integration = Integration.objects.get(pk=channel.pk)
        return [RelationshipTuple(to_object_ref(integration), "reader", to_subject_ref(self.narrow))]

    def seed_dashboards(self) -> list[RelationshipTuple]:
        Dashboard = apps.get_model("dashboards", "Dashboard")
        DashboardWidget = apps.get_model("dashboards", "DashboardWidget")
        with actor_context(self.member):
            dashboards = [
                Dashboard.objects.create_personal(self.member, name=f"Edges {index}", idempotency_key=f"edges-{index}")
                for index in range(2)
            ]
        with system_context(reason="tests.read_through_parent.widgets"):
            for dashboard in dashboards:
                for index, resource in enumerate(("messaging.message", "projects.project")):
                    DashboardWidget.objects.create(
                        dashboard=dashboard, widget_key=f"edges-{index}", kind="resourceView", title=resource,
                        data={"shape": "resourceView", "source": {"resource": resource}, "preset": f"edges-{index}"},
                        sequence=index,
                    )
        return [RelationshipTuple(to_object_ref(dashboards[0]), "viewer", to_subject_ref(self.narrow))]


AUDIT_TIME = re.compile(r"'\d{4}-\d\d-\d\d \d\d:\d\d:\d\d(?:\.\d+)?(?:\+00:00)?'")
INBOX_LABELS = """
query NexusInboxLabels(
  $coverage: InboxCoverageInput $search: InboxSearchInput $options: InboxResultInput $sender: String!
  $circle: String! $scope: InboxGroupScopeInput $page: Int! $size: Int!
) {
  inbox_results(
    coverage: $coverage search: $search options: $options sender: $sender circle: $circle scope: $scope
    page: $page size: $size
  ) { messages { id thread { id modality } } }
}
"""


class ReadThroughParentLabelTests(TransactionTestCase):
    """Display fields on the composed policy: what is declared, who can see them, and where a label shows."""

    def setUp(self):
        call_command("rebac", "sync", verbosity=0)
        call_command("resources", "load", verbosity=0)
        users = get_user_model().objects
        self.admin, _created = users.ensure_bootstrap_admin(
            "labels-admin", email="labels-admin@example.test", password="labels-admin",
        )
        self.member = users.create_person_as_system(
            username="labels-member", email="labels-member@example.test", reason="tests.read_through_parent.labels",
        )
        self.narrow = users.create_person_as_system(
            username="labels-narrow", email="labels-narrow@example.test", reason="tests.read_through_parent.labels",
        )
        self.actors = {"admin": self.admin, "member": self.member, "narrow": self.narrow}
        self.window = DownloadTokenSigner().timestamp()

    def test_the_declared_display_fields_and_who_sees_them_are_pinned(self):
        from angee.graphql.checks import check_display_fields

        declared, audience, readers = display_audience_pins()
        self.assertEqual(declared, DISPLAY_FIELDS)
        self.assertEqual(audience, DISPLAY_AUDIENCE)
        self.assertEqual(readers, HOLDER_READERS)
        with override_settings(**READ_THROUGH_PARENT):
            self.assertEqual(check_display_fields(), [])

    def test_a_label_shows_only_where_the_pinned_audience_predicts(self):
        """Each pinned field a root lists, read for its target's key and label, off and on, as three actors.

        The data is the same, but where the actor may not read the referred record: there the
        field shows its key and label with the switch and nothing without, and ``LABEL_SHOWN``
        names every such field and actor. One more field of the record keeps the scoped read.
        """

        self.seed_narrow_message()
        schemas = GraphQLSchemas.from_discovery()
        shown: set[tuple[str, str]] = set()
        compared: list[str] = []
        for label, fields in DISPLAY_AUDIENCE.items():
            target = apps.get_model(label)
            for spelled, (_holder, kind) in fields.items():
                schema_name, path = spelled.split(" ")
                type_name, field_name = path.split(".")
                core = schemas.graphql_schema(schema_name)
                root = _root(core, type_name)
                if kind != "prefetch" or root is None:
                    continue
                compared.append(spelled)
                target_type = _named(core.type_map[type_name].fields[field_name].type)
                wider = next(leaf for leaf in _leaves(target_type) if leaf not in {"id", *DISPLAY_FIELDS[label]})
                schema = schemas.build(schema_name)
                for role, actor in self.actors.items():
                    with self.subTest(field=spelled, actor=role):
                        labels = " ".join(DISPLAY_FIELDS[label])
                        document = f"query {{ {root} {{ id {field_name} {{ id {labels} }} }} }}"
                        off, on = self.read_both(schema, document, actor)
                        self.assertEqual(off["errors"], [])
                        self.assertEqual(on["errors"], [])
                        before = _compared(off, (str(root),), field_name)["rows"]
                        after = _compared(on, (str(root),), field_name)["rows"]
                        self.assertEqual([row_id for row_id, _value in after], [row_id for row_id, _value in before])
                        for (_row_id, was), (_same, now) in zip(before, after, strict=True):
                            if now == was:
                                continue
                            self.assertIsNone(was)
                            self.assertEqual(set(now), {"id", *DISPLAY_FIELDS[label]})
                            with actor_context(actor):
                                self.assertFalse(target._default_manager.filter(sqid=now["id"]).exists())
                            shown.add((spelled, role))
                        document = f"query {{ {root} {{ id {field_name} {{ id {labels} {wider} }} }} }}"
                        off, on = self.read_both(schema, document, actor)
                        self.assertEqual(
                            _compared(on, (str(root),), field_name), _compared(off, (str(root),), field_name),
                        )
                        self.assertEqual(on["sql"], off["sql"])
        self.assertIn("console MessageType.thread", compared)
        self.assertEqual(shown, LABEL_SHOWN)

    def test_a_stored_override_stops_labels_below_rows_admitted_under_an_implied_action(self):
        """The inbox admits its messages under ``read_inbox``; REBAC proves nothing while an override is stored."""

        ids = budgets.ConsoleDocumentBudgetTests.seed(self, self.member, "labels")
        schema = GraphQLSchemas.from_discovery().build("console")
        variables = budgets.ConsoleDocumentBudgetTests.documents(self, ids)["inbox"][1]

        def thread_reads(read: dict[str, Any]) -> list[bool]:
            table = apps.get_model("messaging", "Thread")._meta.db_table
            return [scoped for scoped in (True, False) for found in budgets.table_reads(read["sql"], scoped=scoped)
                    if found == table]

        off, on = self.read_both(schema, INBOX_LABELS, self.member, variables)
        self.assertEqual(on["data"], off["data"])
        self.assertEqual(thread_reads(off), [True])
        self.assertEqual(thread_reads(on), [False])
        from rebac.models import SchemaOverride, SchemaPermission

        read = SchemaPermission.objects.get(definition__resource_type="messaging/part", name="read")
        # Tightened by what it already requires: the same readers, now under an override.
        SchemaOverride.objects.create(
            kind=SchemaOverride.KIND_TIGHTEN, target=read, expression="message->read",
            reason="tests.read_through_parent.labels",
        )
        off, on = self.read_both(schema, INBOX_LABELS, self.member, variables)
        self.assertEqual(on["data"], off["data"])
        self.assertEqual(thread_reads(on), [True])
        self.assertEqual(on["sql"], off["sql"])

    def read_both(
        self, schema: Any, document: str, actor: Any, variables: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """The document read with the switch off and on, each after a warm read; labels only through pinned fields."""

        with _signed_in(self.window):
            off = self.execute(schema, document, actor, variables or {})
            with override_settings(**READ_THROUGH_PARENT), observed_labels() as seen:
                on = self.execute(schema, document, actor, variables or {})
        assert_labels_through_pinned_fields(seen)
        return off, on

    def execute(self, schema: Any, document: str, actor: Any, variables: dict[str, Any]) -> dict[str, Any]:
        from django.db import connection

        execute_schema(schema, document, variables, user=actor)
        with CaptureQueriesContext(connection) as captured:
            result = execute_schema(schema, document, variables, user=actor)
        return {
            **json.loads(json.dumps(
                {"data": result.data, "errors": sorted(str(error) for error in result.errors or [])}, default=str,
            )),
            # An audit row's time is the one value that differs between two reads of one document.
            "sql": [AUDIT_TIME.sub("'<time>'", query["sql"]) for query in captured.captured_queries],
        }

    def seed_narrow_message(self) -> None:
        """The member's conversations and projects; the narrow actor reads one message and not its thread."""

        ids = budgets.ConsoleDocumentBudgetTests.seed(self, self.member, "labels")
        Message = apps.get_model("messaging", "Message")
        with system_context(reason="tests.read_through_parent.labels"):
            message = Message.objects.get(sqid=ids["message"])
        write_relationships([RelationshipTuple(to_object_ref(message), "reader", to_subject_ref(self.narrow))])
