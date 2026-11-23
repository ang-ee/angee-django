"""The composed host under ``REBAC_INHERIT_THROUGH_LINKS = False``: no document shows a reader a row it reached
only through a link.

``False`` makes REBAC evaluate a narrower policy: an arrow through a link lends only what the linked record
grants directly. The witness reads as people who reach records only through links (a project reader who
follows a task, a work-queue member, a colleague who reads only the member's mail channel) beside the
member and a platform admin:

- every query operation the installed web packages author, for every named schema, with variables drawn
  from the seeded records (``_variable_sets``); none may name a row its reader's REBAC ``read`` denies
  (``unreadable_in``: the objects it returns, the rows a row speaks for, and every public id anywhere in a
  returned string), and none may meet the read-operation refusal of an elevated read that names no policy
  arm. An authored query that runs for no reader fails the test unless ``UNRUN`` says why, so a document
  added later is run or decided;
- every root query that takes no required argument (each model resource's list among them), selecting each
  field of its result that takes none;
- what no row shows: a requester's name, who a comment notifies, what a search counts.

The composed policy narrows with no finding beyond REBAC's own warning that the setting is off.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.checks import WARNING, run_checks
from django.test import override_settings
from graphql import (
    GraphQLEnumType,
    GraphQLInputObjectType,
    GraphQLList,
    GraphQLNonNull,
    GraphQLObjectType,
    GraphQLScalarType,
    OperationDefinitionNode,
    OperationType,
    Visitor,
    print_ast,
    type_from_ast,
    visit,
)
from rebac import RelationshipTuple, actor_context, system_context, to_object_ref, to_subject_ref, write_relationships
from rebac.resources import model_resource_type

from angee.base.addons import is_angee_addon
from angee.base.models.identity import public_id_of
from angee.base.permissions import ShownFacts
from angee.graphql.schema import GraphQLSchemas
from angee.graphql.testing.documents import (
    authored_documents,
    execute_schema,
    result_data,
    unreadable_in,
    unreadable_rows,
    with_typenames,
)
from angee.integrate.models import READY_REGISTRATIONS
from angee.web.build import WebRuntime
from tests.native_graphql_document_budgets import T0, ConsoleDocuments

REFUSAL = "names no policy arm"
"""The read-operation refusal of an elevated read that names no arm (``angee.base.permissions``)."""

AUTHORED_SCHEMAS = ("console", "public")
"""The schemas whose authored documents the console and the public site send."""

UNRUN = {
    "KnowledgeVaultForResource": "addresses a resource-ledger reference the seed declares none of",
    "WorkflowStudio": "addresses a workflow; the witness world loads none",
    "WorkflowStepOutcomes": "addresses a workflow; the witness world loads none",
    "ChannelPairing": "pairing state exists only on a pairing bridge's channel; the seed's channel is manual",
    "DashboardWidgetVisibility": "answers booleans for widget rules the seed declares none of; it names no row",
    "AnalyseAppearance": "fetches a remote site",
    "PreviewImapSample": "connects to a mail server",
    "IntegrateSearchRepositories": "calls a VCS provider",
    "BrowseMountSource": "browses a storage provider",
}
"""Authored queries the witness does not run, and why: each reaches an outside service, not a stored record."""

SHOWN_IDS = {("public", "connectable_accounts"): READY_REGISTRATIONS}
"""Roots that show ids of rows their reader may not read, by the facts the framework declares it shows there."""

FILE_TARGETS = ("connections", "related")
"""The console documents a channel reader cannot complete once the attachment is no longer read through its part."""

FEED = """
query Feed($model: String!, $id: ID!) {
  record_thread(input: {model_label: $model, record_id: $id}) {
    error_code
    messages { id preview parts { id file { id } } }
  }
}
"""

NOTIFICATIONS = """
query Notifications {
  thread_notifications(order_by: [{created_at: asc}]) { id notification_type created_at message { id } }
  thread_notifications_aggregate { aggregate { count } }
}
"""

TASKS = """
query Tasks {
  project_tasks { id title requester { display_name } tags { id name } }
  intake_needs { id requester_user task { id } }
}
"""

REQUESTER_FILTER = """
query RequesterFilter {
  project_tasks(where: {requester_name: {_eq: "Hidden Requester Ltd"}}) { id }
}
"""

MARK_READ = """
mutation MarkRead($id: ID!) { mark_thread_notification_read(id: $id) { id } }
"""

RECORD_FILES = """
query RecordFiles($model: String!, $id: ID!) {
  record_files(model_label: $model, record_id: $id) { can_upload attachments { id file { id filename } } }
}
"""

TIMELINE = """
query Timeline($model: String!, $id: ID!) {
  timeline(model: $model, id: $id) { entries { key kind title detail target_model target_id } kinds { id count } }
}
"""

SEARCH = """
query Search($thread: ID!, $search: String!) {
  thread_message_feed(thread_id: $thread, search: $search) { count messages { id parts { id name file { id } } } }
}
"""


class InheritThroughLinksTests(ConsoleDocuments):
    """Every authored read, the model resource lists and the facts no row shows, under each policy."""

    maxDiff = None

    def person(self, username):
        return get_user_model().objects.create_person_as_system(
            username=username, email=f"{username}@example.test", reason="tests.inherit_through_links",
        )

    def channel_reader(self):
        """A colleague holding only ``reader`` on the member's mail channel."""

        colleague = self.person("budget-channel-reader")
        channel = apps.get_model("messaging", "Channel")._base_manager.get(owner=self.member)
        write_relationships([RelationshipTuple(to_object_ref(channel), "reader", to_subject_ref(colleague))])
        return colleague

    def linked_world(self):
        """The member's project task, followed by a project reader, and a queued task of a work queue.

        The task carries a request filed by a person with an account, whom the project reader reads only
        through the task, a file and a tag; the follow and each task's first comment are made while REBAC
        evaluates the published policy.
        """

        Project = apps.get_model("projects", "Project")
        Task = apps.get_model("projects", "Task")
        Queue = apps.get_model("work", "Queue")
        Membership = apps.get_model("spaces", "Membership")
        Person = apps.get_model("parties", "Person")
        File = apps.get_model("storage", "File")
        FileAttachment = apps.get_model("storage", "FileAttachment")
        reader, queue_member = self.person("linked-project-reader"), self.person("linked-queue-member")
        requester = self.person("linked-hidden-requester")
        with system_context(reason="tests.inherit_through_links.tag"):
            tag = apps.get_model("tags", "Tag").objects.get_or_create(name="Linked")[0]
        with actor_context(self.member):
            project = Project.objects.create(title="Linked project", lead=self.member, target_date=date(2027, 1, 1))
            project.grant_record_access("reader", reader)
            task = Task.objects.create(title="Linked task", project=project, owner=self.member)
            apps.get_model("tags", "TagAssignment").objects.set_for(task, [tag.sqid])
        with system_context(reason="tests.inherit_through_links.world"):
            queue = Queue.objects.create(name="Linked queue", key="LQ", owner=self.member)
            Membership.objects.create(
                group=queue.group_ptr, party=Person.objects.for_user(queue_member), role="member", is_confirmed=True,
            )
            queued = Task.objects.create(title="Queued task", queue=queue, owner=self.member)
            party = Person.objects.for_user(requester)
            Person._base_manager.filter(pk=party.pk).update(display_name="Hidden Requester Ltd")
            apps.get_model("intake", "Need").objects.create(
                task=task, party=party, claimed_email="hidden@example.test", claimed_name="", body="Request",
            )
            file, _created = File.objects.ingest_bytes(b"linked file", filename="linked.txt", owner_id=self.member.pk)
            FileAttachment.objects.attach(file, task)
        with override_settings(REBAC_INHERIT_THROUGH_LINKS=True):
            with actor_context(reader):
                apps.get_model("messaging", "ThreadFollower").objects.subscribe(task, user=reader)
            with actor_context(self.member):
                task.with_actor(self.member).message_post("Posted while links lend whole.")
                queued.with_actor(self.member).message_post("Queued while links lend whole.")
        return {
            "reader": reader, "queue_member": queue_member, "task": task, "queued": queued, "party": party,
            "requester": requester, "project": project, "queue": queue, "file": file, "tag": tag,
        }

    def readers(self, world, channel_reader):
        return {
            "admin": self.admin,
            "member": self.member,
            "project reader": world["reader"],
            "queue member": world["queue_member"],
            "channel reader": channel_reader,
        }

    def test_every_authored_query_shows_no_row_its_reader_cannot_read(self):
        """Each authored query, with the variables the administrator can run it with, as every reader.

        The variable sets are drawn from the seeded records (``_variable_sets``); those the administrator's
        read completes are run as every reader, else the first one is.
        """

        ids = self.seed(self.member, "member")
        channel_reader = self.channel_reader()
        world = self.linked_world()
        readers = self.readers(world, channel_reader)
        records = _records(world, ids, self.member)
        schemas = GraphQLSchemas.from_discovery()
        ran: dict[str, int] = {}
        authored: set[str] = set()
        findings: list[str] = []
        with override_settings(REBAC_INHERIT_THROUGH_LINKS=False):
            for schema_name in AUTHORED_SCHEMAS:
                schema = schemas.build(schema_name)
                for name, operation, document in _authored_queries(schema_name):
                    authored.add(name)
                    if name in UNRUN:
                        continue
                    candidates = list(_variable_sets(schema, operation, records))
                    chosen = [
                        variables for variables in candidates
                        if _witness(schema, document, variables, self.admin).succeeded
                    ][:2] or candidates[:1]
                    for variables in chosen:
                        for role, actor in readers.items():
                            outcome = _witness(schema, document, variables, actor)
                            findings.extend(f"{schema_name}:{name} as {role}: {item}" for item in outcome.findings)
                            ran[name] = ran.get(name, 0) + outcome.succeeded
        self.assertEqual(findings, [], "\n".join(findings))
        self.assertEqual(set(UNRUN) - authored, set(), "UNRUN names a query no installed package authors")
        unrun = sorted(name for name in authored - set(UNRUN) if not ran.get(name))
        self.assertEqual(unrun, [], "authored queries that ran for no reader: run them or say why in UNRUN")

    def test_every_model_list_shows_no_row_its_reader_cannot_read(self):
        """Each root query that names no record, every field that takes no required argument, for each reader."""

        self.seed(self.member, "member")
        channel_reader = self.channel_reader()
        world = self.linked_world()
        readers = self.readers(world, channel_reader)
        schemas = GraphQLSchemas.from_discovery()
        findings: list[str] = []
        with override_settings(REBAC_INHERIT_THROUGH_LINKS=False):
            for schema_name in schemas.names():
                schema = schemas.build(schema_name)
                for root, document in _list_documents(schema):
                    shown = SHOWN_IDS.get((schema_name, root))
                    for role, actor in readers.items():
                        outcome = _witness(schema, document, {}, actor, shown=shown)
                        findings.extend(f"{schema_name}:{root} as {role}: {item}" for item in outcome.findings)
        self.assertEqual(findings, [], "\n".join(findings))

    def test_documents_show_no_row_their_reader_cannot_read(self):
        ids = {role: self.seed(actor, role) for role, actor in (("admin", self.admin), ("member", self.member))}
        channel_reader = self.channel_reader()
        world = self.linked_world()
        reader, queue_member, task, queued = world["reader"], world["queue_member"], world["task"], world["queued"]
        party = world["party"]
        schema = GraphQLSchemas.from_discovery().build("console")
        task_ref = {"model": task._meta.label, "id": task.sqid}
        queued_ref = {"model": queued._meta.label, "id": queued.sqid}
        documents = [
            *(("admin", self.admin, name, *read) for name, read in self.documents(ids["admin"]).items()),
            *(("member", self.member, name, *read) for name, read in self.documents(ids["member"]).items()),
            *(("channel reader", channel_reader, name, *read) for name, read in self.documents(ids["member"]).items()),
            ("project reader", reader, "feed", FEED, task_ref),
            ("project reader", reader, "notifications", NOTIFICATIONS, {}),
            ("project reader", reader, "tasks", TASKS, {}),
            ("project reader", reader, "record files", RECORD_FILES, task_ref),
            ("project reader", reader, "timeline", TIMELINE, task_ref),
            ("queue member", queue_member, "feed", FEED, queued_ref),
            ("queue member", queue_member, "notifications", NOTIFICATIONS, {}),
            ("queue member", queue_member, "timeline", TIMELINE, queued_ref),
            ("member", self.member, "tasks", TASKS, {}),
            ("member", self.member, "timeline", TIMELINE, task_ref),
        ]
        Notification = apps.get_model("messaging", "ThreadNotification")
        Party = apps.get_model("parties", "Party")
        for inherits in (True, False):
            with override_settings(REBAC_INHERIT_THROUGH_LINKS=inherits):
                with actor_context(self.member):
                    posted = [
                        task.with_actor(self.member).message_post(f"Posted with inherits={inherits}."),
                        queued.with_actor(self.member).message_post(f"Queued with inherits={inherits}."),
                    ]
                notified = set(Notification._base_manager.filter(message__in=posted).values_list("user_id", flat=True))
                with self.subTest(inherits=inherits, document="fan-out"):
                    # Fan-out tells only the readers of each record's chatter thread.
                    self.assertEqual({reader.pk, queue_member.pk} <= notified, inherits)
                    self.assertEqual({reader.pk, queue_member.pk} & notified == set(), not inherits)
                for role, actor, name, document, variables in documents:
                    with self.subTest(inherits=inherits, reader=role, document=name):
                        if role == "channel reader" and name in FILE_TARGETS and not inherits:
                            result = execute_schema(schema, document, variables, user=actor)
                            self.assertIsNone(result.data)
                            self.assertEqual(len(result.errors), 1)
                            continue
                        self.assertEqual(unreadable_rows(schema, document, variables, actor=actor), [])
                with self.subTest(inherits=inherits, reader="project reader", document="mark read"):
                    # A row its recipient may no longer read the message of reads as not found.
                    earlier = Notification._base_manager.filter(user=reader).order_by("pk").first()
                    marked = execute_schema(schema, MARK_READ, {"id": public_id_of(earlier)}, user=reader)
                    self.assertIs(not marked.errors, inherits)
                with self.subTest(inherits=inherits, reader="project reader", document="requester"):
                    # A name and an account id are no row: the task names the party, and the
                    # request its account, only where its reader reads the party.
                    party_read = Party.objects.with_actor(reader).filter(pk=party.pk).exists()
                    self.assertIs(party_read, inherits)
                    tasks = result_data(execute_schema(schema, TASKS, {}, user=reader))
                    shown = {row["title"]: row["requester"] for row in tasks["project_tasks"]}
                    named = (shown["Linked task"] or {}).get("display_name") == "Hidden Requester Ltd"
                    self.assertIs(named, party_read)
                    (need,) = (row for row in tasks["intake_needs"] if (row["task"] or {}).get("id") == task.sqid)
                    self.assertIs(need["requester_user"] is not None, party_read)
                    filtered = result_data(execute_schema(schema, REQUESTER_FILTER, {}, user=reader))
                    self.assertIs(bool(filtered["project_tasks"]), party_read)

    def test_a_search_counts_no_file_its_reader_cannot_read(self):
        """A channel reader searches a conversation by a term found only in an attachment file's title.

        The file is read through its message part, a link: under ``False`` the channel reader reads the
        message but not the file, so the term matches nothing, and the count says so.
        """

        ids = self.seed(self.member, "member")
        colleague = self.channel_reader()
        File = apps.get_model("storage", "File")
        file = File._base_manager.get(sqid=ids["file"])
        File._base_manager.filter(pk=file.pk).update(title="Zebra acquisition memo")
        schema = GraphQLSchemas.from_discovery().build("console")
        for inherits in (True, False):
            with override_settings(REBAC_INHERIT_THROUGH_LINKS=inherits), self.subTest(inherits=inherits):
                reads_file = file.with_actor(colleague).has_access("read")
                self.assertIs(reads_file, inherits)
                found = result_data(execute_schema(
                    schema, SEARCH, {"thread": ids["thread"], "search": "Zebra"}, user=colleague,
                ))["thread_message_feed"]
                self.assertEqual(found["count"] > 0, reads_file)
                self.assertEqual(bool(found["messages"]), reads_file)

    def test_the_composed_policy_narrows_with_only_the_settings_own_warning(self):
        def findings(inherits):
            with override_settings(REBAC_INHERIT_THROUGH_LINKS=inherits):
                return {(issue.id, issue.level) for issue in run_checks(databases=["default"])}

        published = findings(True)
        self.assertEqual(findings(False), published | {("rebac.W012", WARNING)})


class _Outcome:
    """What one execution showed: rows REBAC denies, refusals of unnamed elevated reads, and success."""

    def __init__(self) -> None:
        self.findings: list[str] = []
        self.succeeded = 0


def _witness(
    schema: Any, document: str, variables: dict[str, Any], actor: Any, *, shown: ShownFacts | None = None,
) -> _Outcome:
    """Run ``document`` as ``actor``; report rows it names that REBAC denies and unnamed elevated reads.

    ``shown`` names facts the framework declares a root shows beyond its rows' read: ids of that record
    type there are no finding.
    """

    outcome = _Outcome()
    result = execute_schema(schema, with_typenames(document), variables, user=actor)
    outcome.findings.extend(str(error) for error in result.errors or () if REFUSAL in str(error))
    if result.data is not None:
        outcome.findings.extend(
            f"{row.path} names {row.model} {row.public_id}"
            for row in unreadable_in(schema, result.data, actor=actor)
            if shown is None or model_resource_type(apps.get_model(row.model)) != shown.resource_type
        )
    outcome.succeeded = int(not result.errors and result.data is not None)
    return outcome


def _authored_queries(schema_name: str) -> Iterator[tuple[str, OperationDefinitionNode, str]]:
    """Every query operation the installed web packages author for ``schema_name``, with the fragments it spreads.

    The files are those host codegen validates for the schema (``documentFiles`` in ``web-codegen.mjs``).
    """

    names = ("documents.public.ts",) if schema_name == "public" else ("documents.ts", f"documents.{schema_name}.ts")
    configs = {config.name: config for config in apps.get_app_configs() if is_angee_addon(config)}
    paths = sorted(
        path
        for entry in WebRuntime(configs.values()).manifest["addonPackages"]
        for name in names
        for path in (Path(configs[entry["app"]].path) / "web" / entry["sourceRoot"]).rglob(name)
    )
    definitions = [definition for document in authored_documents(*paths) for definition in document.definitions]
    fragments = {
        definition.name.value: definition
        for definition in definitions
        if not isinstance(definition, OperationDefinitionNode)
    }
    for definition in definitions:
        if isinstance(definition, OperationDefinitionNode) and definition.operation is OperationType.QUERY:
            name = definition.name.value if definition.name else print_ast(definition)[:60]
            used = _fragment_closure(definition, fragments)
            yield name, definition, "\n".join(print_ast(node) for node in (definition, *used))


def _fragment_closure(operation: OperationDefinitionNode, fragments: dict[str, Any]) -> list[Any]:
    """The fragments ``operation`` spreads, directly or through other fragments."""

    found: dict[str, Any] = {}
    pending: list[Any] = [operation]
    while pending:
        spreads: list[str] = []

        class _Spreads(Visitor):
            def enter_fragment_spread(self, spread: Any, *_args: Any) -> None:
                spreads.append(spread.name.value)

        visit(pending.pop(), _Spreads())
        for name in spreads:
            if name in fragments and name not in found:
                found[name] = fragments[name]
                pending.append(fragments[name])
    return list(found.values())


def _records(world: dict[str, Any], ids: dict[str, str], member: Any) -> list[tuple[str, str, str]]:
    """The seeded records an authored query may address, as ``(model label, resource type, public id)``."""

    Channel = apps.get_model("messaging", "Channel")
    Vault = apps.get_model("knowledge", "Vault")
    Page = apps.get_model("knowledge", "Page")
    with actor_context(member):
        vault = Vault.objects.create_for(member, name="Witness vault")
        page = Page.objects.create_in(vault, title="Witness page")
    rows = [
        world["task"], world["queued"], world["project"], world["party"], world["file"], world["queue"],
        world["tag"], Channel._base_manager.get(owner=member), member, vault, page,
    ]
    found = [(row._meta.label, model_resource_type(type(row)) or "", public_id_of(row)) for row in rows]
    for key, label in (("thread", "messaging.Thread"), ("message", "messaging.Message"),
                       ("file", "storage.File"), ("person", "parties.Person")):
        found.append((label, model_resource_type(apps.get_model(label)) or "", ids[key]))
    return found


_INTS = {"page": 1, "offset": 0, "depth": 1}
_STRINGS = {"axis": "account", "backendclass": "local_folder"}
_OMIT = object()


def _variable_sets(
    schema: Any, operation: OperationDefinitionNode, records: list[tuple[str, str, str]],
) -> Iterator[dict]:
    """Variables for ``operation``: one set per seeded record its id variables may address.

    A variable is filled by its type and name: a model label takes the record's label, a type or subject
    its resource type, and an id its public id, record by record; a ``target`` string takes the record as
    the inbox addresses one; numbers, flags, dates and enums take a value of their type; an input that may
    be omitted is left out.
    """

    definitions = {
        variable.variable.name.value: type_from_ast(schema._schema, variable.type)
        for variable in operation.variable_definitions or ()
    }
    seen: set[str] = set()
    for record in records:
        values: dict[str, Any] = {}
        for name, kind in definitions.items():
            value = _value(name, kind, record)
            if value is not _OMIT:
                values[name] = value
        key = repr(sorted(values.items(), key=lambda item: item[0]))
        if key not in seen:
            seen.add(key)
            yield values


def _value(name: str, kind: Any, record: tuple[str, str, str]) -> Any:
    label, resource_type, public_id = record
    required = isinstance(kind, GraphQLNonNull)
    inner = kind.of_type if required else kind
    lowered = name.lower()
    if not required and not ("model" in lowered or lowered in ("id", "recordid", "rootid")):
        return _OMIT
    if isinstance(inner, GraphQLList):
        if lowered == "models":
            return [label]
        item = inner.of_type
        element = _value(name, item if isinstance(item, GraphQLNonNull) else GraphQLNonNull(item), record)
        listed = isinstance(inner.of_type, GraphQLNonNull) and (
            isinstance(inner.of_type.of_type, GraphQLInputObjectType | GraphQLEnumType)
            or "id" in lowered or "record" in lowered
        )
        return [element] if element is not _OMIT and listed else []
    if isinstance(inner, GraphQLInputObjectType):
        filled = {}
        for field_name, field in inner.fields.items():
            value = _value(field_name, field.type, record)
            if value is not _OMIT:
                filled[field_name] = value
        return filled
    if isinstance(inner, GraphQLEnumType):
        return next(iter(inner.values))
    if not isinstance(inner, GraphQLScalarType):
        return _OMIT
    named = inner.name
    if lowered in ("targettype", "type"):
        return resource_type
    if lowered == "subject":
        return f"{resource_type}:{public_id}"
    if named in ("String", "ID") and "model" in lowered:
        return label
    if lowered in _STRINGS:
        return _STRINGS[lowered]
    if lowered == "target":
        return f"file:{public_id}" if label == "storage.File" else public_id
    if named == "ID" or (named == "String" and (lowered.endswith("id") or lowered in (
        "task", "page", "file", "project", "decision", "run", "thread", "queue", "parent", "source",
    ))):
        return public_id
    if named == "Int":
        return _INTS.get(lowered, 25)
    if named == "Boolean":
        return False
    if named == "DateTime":
        return (T0 - timedelta(days=30) if "start" in lowered else T0 + timedelta(days=30)).isoformat()
    if named == "Date":
        day = T0.date()
        return (day - timedelta(days=30) if "start" in lowered else day + timedelta(days=30)).isoformat()
    if named == "JSON":
        return {}
    return ""


def _list_documents(schema: Any) -> Iterator[tuple[str, str]]:
    """A document per root query field that takes no required argument: every field of its result that takes none.

    Model resource lists among them, and every other root a reader can ask without naming a record.
    """

    query = schema._schema.query_type
    for root, field in sorted(query.fields.items()):
        if any(isinstance(argument.type, GraphQLNonNull) for argument in field.args.values()):
            continue
        row = field.type
        while isinstance(row, GraphQLNonNull | GraphQLList):
            row = row.of_type
        if isinstance(row, GraphQLObjectType):
            yield root, f"query List {{ {root} {{ {_selection(row, depth=1)} }} }}"


def _selection(kind: GraphQLObjectType, *, depth: int) -> str:
    """Every field of ``kind`` that takes no required argument: scalars, and its objects' own fields."""

    parts = []
    for name, field in kind.fields.items():
        if any(isinstance(argument.type, GraphQLNonNull) for argument in field.args.values()):
            continue
        target = field.type
        while isinstance(target, GraphQLNonNull | GraphQLList):
            target = target.of_type
        if isinstance(target, GraphQLObjectType):
            if depth > 0 and (inner := _selection(target, depth=depth - 1)):
                parts.append(f"{name} {{ {inner} }}")
        elif isinstance(target, GraphQLScalarType | GraphQLEnumType):
            parts.append(name)
    return " ".join(parts)
