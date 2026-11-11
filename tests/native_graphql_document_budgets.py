"""Fixed SQL ceilings for the console app's core reads on the composed model graph.

The documents are the console app's own reads: the inbox's first page of
conversations, a thread transcript, a person record form, the projects list, the
current user, and the inbox's selected message with its attachment's connections
and pinned Related target. Each runs as a platform admin and as a member over the same
seeded shape. Each read is measured after one warm execution, so process-wide
caches filled by earlier test modules do not move the count.
"""

from datetime import UTC, date, datetime, timedelta

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TransactionTestCase
from rebac import actor_context, system_context

from angee.base.testing.queries import capture_planner_memory, statement_startswith, supports_planner_memory
from angee.graphql.schema import GraphQLSchemas
from angee.graphql.testing.documents import measure_graphql_document
from angee.messaging.backends import ParsedHandle, ParsedMessage, ParsedPart, ParsedRecipient

T0 = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)

INBOX = """
query NexusInboxResults(
  $coverage: InboxCoverageInput
  $search: InboxSearchInput
  $options: InboxResultInput
  $sender: String!
  $circle: String!
  $scope: InboxGroupScopeInput
  $page: Int!
  $size: Int!
) {
  inbox_results(
    coverage: $coverage
    search: $search
    options: $options
    sender: $sender
    circle: $circle
    scope: $scope
    page: $page
    size: $size
  ) {
    count
    message_count
    rows {
      id latest count part_count message_id
      part { ...MessagePartFields }
      file {
        id filename url size_bytes
        mime_type { mime_type }
      }
      fragment { id text }
    }
    messages { ...InboxMessageFields }
  }
}

fragment InboxMessageFields on MessageType {
  id preview title platform direction status sent_at created_at
  sender { ...MessageSenderFields }
  thread { id modality title { text } }
  channel { id display_name }
}

fragment MessageSenderFields on HandleType {
  id display_name value party_link_confirmed
  avatar { id url }
  party { id display_name display_avatar { id url } }
  merged_into { id party_link_confirmed party { id display_name display_avatar { id url } } }
}

fragment MessagePartFields on PartType {
  id name type position role disposition cid
  parent { id type }
  fragment { id text }
  file {
    id filename title size_bytes url
    mime_type { mime_type label }
  }
}
"""

TRANSCRIPT = """
query MessagingThreadTranscript(
  $threadId: ID!
  $limit: Int!
  $beforeCursor: String
  $throughCursor: String
  $afterCursor: String
  $anchor: String! = ""
  $allCopies: Boolean! = false
) {
  thread_message_feed(
    thread_id: $threadId
    all_copies: $allCopies
    before_cursor: $beforeCursor
    through_cursor: $throughCursor
    after_cursor: $afterCursor
    anchor: $anchor
    limit: $limit
  ) {
    ...MessageFeedWindowFields
    newer_cursor has_newer has_newer_than_before
    messages { ...TranscriptMessageFields }
  }
}

fragment MessageFeedWindowFields on MessageFeedPage {
  count older_cursor has_older has_more_in_window has_older_than_through
}

fragment TranscriptMessageFields on MessageType {
  id channel { id display_name }
  feed_order_key direction preview
  parent { ...MessageReplyFields }
  message_type sent_at created_at
  sender { ...MessageSenderFields }
  parts { ...MessagePartFields }
  reaction_groups { ...ReactionGroupFields }
}

fragment MessageReplyFields on MessageType {
  id preview message_type
  sender { ...MessageSenderFields }
  thread { id modality }
}

fragment MessageSenderFields on HandleType {
  id display_name value party_link_confirmed
  avatar { id url }
  party { id display_name display_avatar { id url } }
  merged_into { id party_link_confirmed party { id display_name display_avatar { id url } } }
}

fragment MessagePartFields on PartType {
  id name type position role disposition cid
  parent { id type }
  fragment { id text }
  file {
    id filename title size_bytes url
    mime_type { mime_type label }
  }
}

fragment ReactionGroupFields on MessageReactionGroupType {
  reaction count self_reacted handles { id display_name value }
}
"""

PERSON = """
query HomePersonRecord($id: String!) {
  people_by_pk(id: $id) {
    id display_name redacted_fields
    name_prefix given_name additional_name family_name name_suffix nickname
    birthday anniversary notes first_met_note
    created_at updated_at created_by_label updated_by_label
    handle_count circle_names concrete_kind
    introduced_by { id display_name }
    avatar { id url }
    display_avatar { id url }
    handles { id display_name value platform label is_preferred }
    addresses { id label street extended city region postal_code country is_primary }
    tags { id name }
  }
}
"""

PROJECTS = """
query ResourceList(
  $where: projects_bool_exp
  $order_by: [projects_order_by!]
  $limit: Int
  $offset: Int
) {
  projects(where: $where, order_by: $order_by, limit: $limit, offset: $offset) {
    id title display_name current_milestone { id name }
    status lead { id display_name } target_date updated_at tags { id name }
  }
  projects_aggregate(where: $where) { aggregate { count } }
}
"""

IDENTITY = """
query AngeeCurrentUser($refs: [String!]!) {
  current_user {
    id username
    firstName: first_name
    lastName: last_name
    email
    isStaff: is_staff
    isActive: is_active
    preferences
    roleRefs: role_refs
    permitted(refs: $refs)
  }
}
"""

# The inbox's selected message, the attachment's connections and its pinned Related target.

SENDER = """
fragment MessageSenderFields on HandleType {
  id display_name value party_link_confirmed
  avatar { id url }
  party { id display_name display_avatar { id url } }
  merged_into { id party_link_confirmed party { id display_name display_avatar { id url } } }
}
"""

PART = """
fragment MessagePartFields on PartType {
  id name type position role disposition cid
  parent { id type }
  fragment { id text }
  file {
    id filename content_hash title size_bytes url
    mime_type { mime_type label }
  }
}
"""

INBOX_MESSAGE = """
fragment InboxMessageFields on MessageType {
  id preview title platform direction status sent_at created_at
  sender { ...MessageSenderFields }
  thread { id modality title { text } }
  channel { id display_name }
}
"""

READER = """
fragment MessageReplyFields on MessageType {
  id preview message_type
  sender { ...MessageSenderFields }
  thread { id modality }
}

fragment MessageReaderFields on MessageType {
  id platform
  thread { id modality }
  channel { id display_name }
  title preview sent_at created_at starred
  sender { ...MessageSenderFields }
  parent { ...MessageReplyFields }
  participants { id role handle { ...MessageSenderFields } }
  parts { ...MessagePartFields }
}
"""

SELECTED = (
    """
query NexusInboxMessage(
  $id: ID!
  $coverage: InboxCoverageInput
  $search: InboxSearchInput
  $sender: String!
  $circle: String!
) {
  inbox_message(id: $id, coverage: $coverage, search: $search, sender: $sender, circle: $circle) {
    matches
    uses { part_id target count }
    message {
      ...InboxMessageFields
      ...MessageReaderFields
      thread { id conversation_label }
      received_at external_id
    }
  }
}
"""
    + INBOX_MESSAGE
    + READER
    + SENDER
    + PART
)

CONNECTIONS = (
    """
query NexusInboxConnections($target: String!, $text: String!, $oldest: Boolean!, $page: Int!, $size: Int!) {
  inbox_connections(target: $target, text: $text, oldest: $oldest, page: $page, size: $size) {
    count
    message_count
    rows {
      id label role provenance confidence message_id
      part { ...MessagePartFields }
      handle { ...MessageSenderFields platform }
      src_id dst_id
    }
    messages { ...InboxMessageFields }
  }
}
"""
    + INBOX_MESSAGE
    + SENDER
    + PART
)

RELATED = (
    """
query NexusInboxRelatedTarget($target: String!, $source: String!) {
  inbox_related_target(target: $target, source: $source) {
    id kind label count unit
    source { ...InboxMessageFields }
    part { ...MessagePartFields }
    file { id filename content_hash url size_bytes mime_type { mime_type } }
    fragment { id text }
  }
}
"""
    + INBOX_MESSAGE
    + SENDER
    + PART
)

# The rows each read returns, so a read that loses its rows cannot pass under its ceiling.
ROWS = {
    "inbox": lambda data: len(data["inbox_results"]["messages"]),
    "transcript": lambda data: len(data["thread_message_feed"]["messages"]),
    "person": lambda data: len(data["people_by_pk"]["handles"]) + len(data["people_by_pk"]["addresses"]),
    "projects": lambda data: data["projects_aggregate"]["aggregate"]["count"],
    "identity": lambda data: int(data["current_user"] is not None),
    "selected": lambda data: len(data["inbox_message"]["message"]["parts"]) + len(data["inbox_message"]["uses"]),
    "connections": lambda data: len(data["inbox_connections"]["rows"]),
    "related": lambda data: data["inbox_related_target"]["count"],
}

# (actor, document): rows, statement ceiling. Measured on the SQLite composed host;
# a platform admin reads both actors' messages and projects.
BUDGETS = {
    # REBAC 0.31 compiles one actor-independent statement per scope, where 0.30 decided each
    # actor's constant facts first and pruned the policy. A witness statement decides whether the
    # actor holds a permission's constant arms (``admin->member``): an admin then reads a statement
    # of those arms alone, and a member's statement leaves them out. Since 0.31.5 the request keeps
    # that answer wherever the arms' types read no model column, as on every read here, so each
    # actor pays one witness per distinct arm per request.
    ("admin", "inbox"): (18, 13),  # 0.31: 15 -> 14 statements (3 witnesses); 0.33.1: 13
    ("admin", "transcript"): (3, 31),  # 0.31: 35 -> 32 statements (3 witnesses); 0.33.1: 31
    ("admin", "person"): (2, 20),  # 0.31: 29 -> 20 statements (2 witnesses)
    ("admin", "projects"): (6, 10),  # 0.31: 15 -> 10 statements (2 witnesses)
    ("admin", "identity"): (1, 1),
    ("member", "inbox"): (9, 14),  # 0.31: 123 -> 14 statements (3 witnesses); id sets are CTEs
    # The feed reads its messages under ``read_inbox`` and the reply-parent fields read ``read``
    # through the relation guard; 0.31 compiles both graphs inline instead of deciding their sets.
    ("member", "transcript"): (3, 32),  # 0.31: 250 -> 32 statements (3 witnesses)
    # The PostgreSQL gate below additionally budgets the sum of SELECT allocations.
    # REBAC 0.33.1 decides constant arms inherited through field arrows in a witness statement;
    # the member person read has no own witness, so it pays that one first-read statement.
    ("member", "person"): (2, 21),
    ("member", "projects"): (3, 10),
    ("member", "identity"): (1, 1),
    # The inbox's selected message, the attachment's connections and its Related target.
    ("admin", "selected"): (7, 26),
    ("admin", "connections"): (1, 17),
    ("admin", "related"): (1, 18),
    ("member", "selected"): (7, 27),
    ("member", "connections"): (1, 18),
    ("member", "related"): (1, 19),
}


# SELECT count and total planner allocation rounded up to MiB, measured on PostgreSQL 17,
# the CI server, with REBAC 0.33.1: person allocates 496,957 KiB across 19 SELECTs (ceil =
# 486 MiB; its two audit INSERTs keep the 21-statement document ceiling), projects 150,705
# KiB across all 10 SELECTs (ceil = 148 MiB). PostgreSQL 18 allocates slightly less and is
# not the gate's reference.
PLANNER_BUDGETS = {
    # Storage's deliberate trash-hiding file read split adds 8 MiB per file permission read; falls with rebac 0.34.
    # REBAC 0.33.1: the inherited witness adds one SELECT; 496,957 KiB across 19 SELECTs.
    "person": (19, 486),
    "projects": (10, 148),
    # The member's inbox selection reads plan the merged policy's non-admin id sets, the largest
    # remaining planner cost: 1,325,377 / 764,084 / 731,636 KiB. Ratchet down as they fall.
    "selected": (26, 1295),
    "connections": (17, 747),
    "related": (19, 715),
}


class ConsoleDocumentBudgetTests(TransactionTestCase):
    """Each console read stays within its fixed statement ceiling."""

    def setUp(self):
        call_command("rebac", "sync", verbosity=0)
        # A provisioned host carries the master and install tiers, storage's default drive among them.
        call_command("resources", "load", verbosity=0)
        users = get_user_model().objects
        self.admin, _created = users.ensure_bootstrap_admin(
            "budget-admin",
            email="budget-admin@example.test",
            password="budget-admin",
        )
        self.member = users.create_person_as_system(
            username="budget-member",
            email="budget-member@example.test",
            reason="tests.document_budgets.member",
        )

    def seed(self, actor, key):
        """Give ``actor`` three email conversations, a contact and three projects; return the read ids."""

        Message = apps.get_model("messaging", "Message")
        Handle = apps.get_model("parties", "Handle")
        Person = apps.get_model("parties", "Person")
        Project = apps.get_model("projects", "Project")
        Milestone = apps.get_model("projects", "Milestone")
        TagAssignment = apps.get_model("tags", "TagAssignment")
        contacts = [
            ParsedHandle(platform="email", value=f"{name.lower()}.{key}@example.test", display_name=f"{name} Contact")
            for name in ("Ada", "Grace", "Alan")
        ]
        own = ParsedHandle(platform="email", value=actor.email)
        parsed = []
        for thread in range(3):
            for index in range(3):
                sender = contacts[(thread + index) % 3]
                parsed.append(
                    ParsedMessage(
                        external_id=f"<{key}-{thread}-{index}@example.test>",
                        platform="email",
                        subject=f"Conversation {thread}",
                        sender=sender,
                        recipients=(ParsedRecipient(handle=own),),
                        sent_at=T0 + timedelta(days=thread, hours=index),
                        in_reply_to=f"<{key}-{thread}-{index - 1}@example.test>" if index else "",
                        body=ParsedPart(
                            type="multipart/mixed",
                            children=(
                                ParsedPart(text=f"Message {index} of conversation {thread}."),
                                *(
                                    (
                                        ParsedPart(
                                            type="application/pdf",
                                            disposition="attachment",
                                            name="brief.pdf",
                                            content=f"%PDF-1.4 {key} {thread}".encode(),
                                        ),
                                    )
                                    if index == 0
                                    else ()
                                ),
                            ),
                        ),
                    )
                )
        with system_context(reason="tests.document_budgets.messages"):
            vendor = apps.get_model("integrate", "Vendor").objects.create(slug=f"budget-{key}", display_name=key)
            channel = apps.get_model("messaging", "Channel").objects.create(
                vendor=vendor,
                owner=actor,
                backend_class="manual",
                lifecycle="connected",
                display_name=f"{key} mail",
            )
            messages = Message.objects.ingest(parsed, channel=channel)
            tag = apps.get_model("tags", "Tag").objects.get_or_create(name="Budget")[0]
            handles = [Handle.objects.upsert(platform="email", value=contact.value) for contact in contacts]
        with actor_context(actor):
            for message in messages[-3:]:
                Message.objects.set_reaction(message, reaction="+1", action="add", user=actor)
            introducer = Person.objects.create(display_name=f"Charles {key}", given_name="Charles")
            person = Person.objects.create(
                display_name=f"Ada {key}",
                given_name="Ada",
                family_name="Lovelace",
                nickname="Ada",
                birthday=date(1815, 12, 10),
                notes="Analyst.",
                first_met_note="Met at a lecture.",
                introduced_by=introducer,
            )
            person.with_actor(actor).confirm(aspect="handle", handle=handles[0], values={})
            apps.get_model("parties", "Address").objects.create(
                party=person,
                label="home",
                street="12 St James's Square",
                city="London",
                postal_code="SW1Y 4JH",
                country="GB",
            )
            TagAssignment.objects.set_for(person, [tag.sqid])
            for index in range(3):
                project = Project.objects.create(
                    title=f"{key} project {index}",
                    lead=actor,
                    target_date=date(2027, 1, index + 1),
                )
                project.set_current_milestone(Milestone.objects.create(project=project, name=f"Phase {index}"))
                TagAssignment.objects.set_for(project, [tag.sqid])
        with system_context(reason="tests.document_budgets.attachment"):
            attached = apps.get_model("messaging", "Part").objects.get(message=messages[-3], file__isnull=False)
        return {
            "thread": messages[-1].thread.sqid,
            "person": person.sqid,
            "message": messages[-3].sqid,
            "file": attached.file.sqid,
        }

    def documents(self, ids):
        return {
            "inbox": (
                INBOX,
                {
                    "coverage": None,
                    "search": None,
                    "options": {"lens": "conversations", "roles": [], "oldest": False, "timezone": "UTC"},
                    "sender": "",
                    "circle": "",
                    "scope": None,
                    "page": 1,
                    "size": 25,
                },
            ),
            "transcript": (
                TRANSCRIPT,
                {
                    "threadId": ids["thread"],
                    "limit": 50,
                    "beforeCursor": None,
                    "throughCursor": None,
                    "afterCursor": None,
                    "anchor": "",
                    "allCopies": False,
                },
            ),
            "person": (PERSON, {"id": ids["person"]}),
            "projects": (PROJECTS, {"where": None, "order_by": [{"updated_at": "desc"}], "limit": 25, "offset": 0}),
            "identity": (IDENTITY, {"refs": []}),
            "selected": (
                SELECTED,
                {
                    "id": ids["message"],
                    "coverage": None,
                    "search": None,
                    "sender": "",
                    "circle": "",
                },
            ),
            "connections": (
                CONNECTIONS,
                {
                    "target": f"file:{ids['file']}",
                    "text": "",
                    "oldest": False,
                    "page": 1,
                    "size": 25,
                },
            ),
            "related": (RELATED, {"target": f"file:{ids['file']}", "source": ids["message"]}),
        }

    def test_console_reads_stay_within_their_fixed_ceilings(self):
        actors = {"admin": self.admin, "member": self.member}
        ids = {role: self.seed(actor, role) for role, actor in actors.items()}
        schema = GraphQLSchemas.from_discovery().build("console")
        for role, actor in actors.items():
            for name, (document, variables) in self.documents(ids[role]).items():
                with self.subTest(actor=role, document=name):
                    rows, statements = BUDGETS[role, name]
                    measure_graphql_document(schema, document, variables, actor=actor)
                    read = measure_graphql_document(schema, document, variables, actor=actor)
                    self.assertEqual(ROWS[name](read.data), rows)
                    read.assert_ceiling(statements=statements)

    def test_member_reads_planner_memory(self):
        """Budget every SELECT planned by the member's person, projects and inbox selection documents."""

        self.assertTrue(supports_planner_memory(), "EXPLAIN MEMORY requires PostgreSQL 17 or later")
        self.seed(self.admin, "admin")
        documents = self.documents(self.seed(self.member, "member"))
        schema = GraphQLSchemas.from_discovery().build("console")
        for name in PLANNER_BUDGETS:
            with self.subTest(document=name):
                document, variables = documents[name]
                measure_graphql_document(schema, document, variables, actor=self.member)
                with capture_planner_memory(lambda sql: statement_startswith(sql, "SELECT")) as plans:
                    read = measure_graphql_document(schema, document, variables, actor=self.member)
                rows, statements = BUDGETS["member", name]
                self.assertEqual(ROWS[name](read.data), rows)
                self.assertLessEqual(read.statements, statements)
                selects, memory_mib = PLANNER_BUDGETS[name]
                self.assertEqual(len(plans), selects)
                self.assertTrue(all(plan.allocated_kib > 0 for plan in plans))
                allocated_kib = sum(plan.allocated_kib for plan in plans)
                self.assertLessEqual(
                    allocated_kib,
                    memory_mib * 1024,
                    f"{name}: PostgreSQL allocated {allocated_kib} KiB across {selects} SELECTs; "
                    f"ceiling {memory_mib} MiB. Ratchet downward when cost falls; never raise for a regression.",
                )
