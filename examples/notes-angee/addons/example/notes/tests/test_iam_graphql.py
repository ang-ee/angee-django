"""HTTP GraphQL tests for IAM session verbs."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, cast

import reversion
from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db.models import Count
from django.test import Client, TransactionTestCase
from rebac import system_context

Note = apps.get_model("notes", "Note")
User = get_user_model()


class IAMGraphQLTests(TransactionTestCase):
    """Drive IAM GraphQL fields through the public HTTP endpoint."""

    def setUp(self) -> None:
        call_command("rebac", "sync", verbosity=0)
        call_command("resources", "load", include_demo=True, allow_non_dev=True, verbosity=0)
        with system_context(reason="test-setup"):
            self.welcome = Note.objects.get(title="Welcome to Angee")
            self.alice = User.objects.get(username="alice")
            self.bob = User.objects.get(username="bob")
            self.admin = User.objects.get(username="admin")
            self.named_owner = User.objects.create_user(
                username="named-owner",
                email="named-owner@example.com",
                password="!",
                first_name="Named",
                last_name="Owner",
            )
            self.named_note = Note.objects.create(
                title="Named owner note",
                body="Display label fixture",
                created_by=self.named_owner,
                updated_by=self.bob,
            )
        self.client = Client()

    def test_login_current_user_and_logout_use_session_cookie(self) -> None:
        anonymous = self.graphql("query { currentUser { username } }")
        self.assertEqual(anonymous["data"], {"currentUser": None})

        logged_in = self.graphql(
            """
            mutation {
              login(username: "alice", password: "alice") {
                ok
                user { username isStaff }
              }
            }
            """
        )

        self.assertEqual(
            logged_in["data"],
            {
                "login": {
                    "ok": True,
                    "user": {"username": "alice", "isStaff": True},
                }
            },
        )

        current = self.graphql("query { currentUser { username isStaff } }")
        self.assertEqual(
            current["data"],
            {"currentUser": {"username": "alice", "isStaff": True}},
        )

        logged_out = self.graphql("mutation { logout }")
        self.assertEqual(logged_out["data"], {"logout": True})

        anonymous_again = self.graphql("query { currentUser { username } }")
        self.assertEqual(anonymous_again["data"], {"currentUser": None})

    def test_notes_page_and_update_by_relay_id(self) -> None:
        self.graphql(
            """
            mutation {
              login(username: "alice", password: "alice") {
                ok
              }
            }
            """
        )
        page = self.graphql(
            """
            query {
              notes(pagination: { limit: 10 }) {
                totalCount
                results {
                  id
                  title
                  tags
                  wordCount
                }
              }
            }
            """
        )

        notes = page["data"]["notes"]
        self.assertGreaterEqual(notes["totalCount"], 3)
        welcome = next(node for node in notes["results"] if node["title"] == "Welcome to Angee")
        relay_id = welcome["id"]
        self.assertNotEqual(relay_id, self.welcome.sqid)
        self.assertIn("backend", welcome["tags"])
        self.assertGreater(welcome["wordCount"], 0)

        by_id = self.graphql(
            """
            query NoteByID($id: ID!) {
              note(id: $id) { id title }
            }
            """,
            {"id": relay_id},
        )
        self.assertEqual(
            by_id["data"]["note"],
            {"id": relay_id, "title": "Welcome to Angee"},
        )

        updated = self.graphql(
            """
            mutation UpdateNote($id: ID!) {
              updateNote(
                data: {id: $id, title: "Welcome through Relay"}
              ) {
                id
                title
              }
            }
            """,
            {"id": relay_id},
        )
        self.assertEqual(
            updated["data"]["updateNote"],
            {"id": relay_id, "title": "Welcome through Relay"},
        )

    def test_note_can_have_in_review_status(self) -> None:
        alice = Client()
        self.login(alice, "alice")
        page = self.post(
            alice,
            """
            query {
              notes(pagination: { limit: 20 }) {
                results { title status }
              }
            }
            """,
        )["data"]["notes"]["results"]

        planning = next(node for node in page if node["title"] == "Quarterly planning")
        self.assertEqual(planning["status"], "IN_REVIEW")

    def test_note_exposes_scalar_audit_ids_and_stamps_updates(self) -> None:
        alice = Client()
        self.login(alice, "alice")
        alice_notes = self.post(
            alice,
            """
            query {
              notes(pagination: { limit: 20 }) {
                results { id title createdBy updatedBy }
              }
            }
            """,
        )["data"]["notes"]["results"]
        planning = next(node for node in alice_notes if node["title"] == "Quarterly planning")

        self.assertEqual(planning["createdBy"], self.alice.public_id)
        self.assertEqual(planning["updatedBy"], self.admin.public_id)
        self.assertIsInstance(planning["createdBy"], str)
        self.assertIsInstance(planning["updatedBy"], str)
        self.assertTrue(planning["createdBy"])
        self.assertTrue(planning["updatedBy"])

        updated = self.post(
            alice,
            """
            mutation UpdateNote($id: ID!) {
              updateNote(
                data: {id: $id, title: "Quarterly planning updated"}
              ) {
                id
                title
                createdBy
                updatedBy
              }
            }
            """,
            {"id": planning["id"]},
        )

        self.assertEqual(
            updated["data"]["updateNote"],
            {
                "id": planning["id"],
                "title": "Quarterly planning updated",
                "createdBy": self.alice.public_id,
                "updatedBy": self.alice.public_id,
            },
        )

    def test_note_audit_labels_are_strings_without_user_projection(self) -> None:
        self.login(self.client, "admin")
        notes = self.graphql(
            """
            query {
              notes(pagination: { limit: 100 }) {
                results {
                  title
                  createdBy
                  createdByLabel
                  updatedBy
                  updatedByLabel
                }
              }
            }
            """
        )["data"]["notes"]["results"]
        named = next(node for node in notes if node["title"] == self.named_note.title)
        planning = next(node for node in notes if node["title"] == "Quarterly planning")

        self.assertEqual(named["createdBy"], self.named_owner.public_id)
        self.assertEqual(named["createdByLabel"], "Named Owner")
        self.assertEqual(named["updatedBy"], self.bob.public_id)
        self.assertEqual(named["updatedByLabel"], "bob")
        self.assertEqual(planning["createdBy"], self.alice.public_id)
        self.assertEqual(planning["createdByLabel"], "alice")
        self.assertEqual(planning["updatedBy"], self.admin.public_id)
        self.assertEqual(planning["updatedByLabel"], "admin")

        note_type = self.graphql(
            """
            query {
              __type(name: "NoteType") {
                fields {
                  name
                  type { kind name }
                }
              }
            }
            """
        )["data"]["__type"]
        fields = {
            field["name"]: field["type"]
            for field in note_type["fields"]
        }

        self.assertEqual(fields["createdBy"], {"kind": "SCALAR", "name": "ID"})
        self.assertEqual(fields["updatedBy"], {"kind": "SCALAR", "name": "ID"})
        self.assertEqual(fields["createdByLabel"], {"kind": "SCALAR", "name": "String"})
        self.assertEqual(fields["updatedByLabel"], {"kind": "SCALAR", "name": "String"})
        self.assertNotIn("user", fields)
        self.assertNotIn("email", fields)
        self.assertNotIn("isStaff", fields)

    def test_note_revisions_are_actor_scoped(self) -> None:
        with system_context(reason="test-setup"):
            with reversion.create_revision():
                self.welcome.body = "First reviewed body"
                self.welcome.save()
                reversion.set_comment("first body")
            with reversion.create_revision():
                self.welcome.body = "Second reviewed body"
                self.welcome.save()
                reversion.set_comment("second body")

        alice = Client()
        self.login(alice, "alice")
        welcome_id = next(node["id"] for node in self.notes(alice)["results"] if node["title"] == "Welcome to Angee")
        visible = self.post(
            alice,
            """
            query NoteRevisions($id: ID!) {
              noteRevisions(id: $id) {
                id
                createdAt
                comment
                body
              }
            }
            """,
            {"id": welcome_id},
        )

        revisions = visible["data"]["noteRevisions"]
        self.assertEqual(
            [revision["body"] for revision in revisions],
            ["Second reviewed body", "First reviewed body"],
        )
        self.assertEqual(
            [revision["comment"] for revision in revisions],
            ["second body", "first body"],
        )
        self.assertTrue(all(revision["id"] for revision in revisions))
        self.assertTrue(all(revision["createdAt"] for revision in revisions))

        bob = Client()
        self.login(bob, "bob")
        scoped_out = self.post(
            bob,
            """
            query NoteRevisions($id: ID!) {
              noteRevisions(id: $id) { id }
            }
            """,
            {"id": welcome_id},
        )
        self.assertEqual(scoped_out["data"]["noteRevisions"], [])

    def test_notes_paginate_in_declared_order(self) -> None:
        alice = Client()
        self.login(alice, "alice")
        full = self.notes(alice)
        ordered = [node["title"] for node in full["results"]]

        first_page = self.post(
            alice,
            """
            query {
              notes(pagination: { offset: 0, limit: 1 }) {
                totalCount
                pageInfo { offset limit }
                results { title }
              }
            }
            """,
        )["data"]["notes"]
        second_page = self.post(
            alice,
            """
            query {
              notes(pagination: { offset: 1, limit: 1 }) {
                results { title }
              }
            }
            """,
        )["data"]["notes"]

        # Meta.ordering is ("-updated_at", "title", "sqid"); offset pages
        # follow that order without overlap.
        self.assertEqual(first_page["results"][0]["title"], ordered[0])
        self.assertEqual(first_page["pageInfo"], {"offset": 0, "limit": 1})
        self.assertGreater(first_page["totalCount"], 1)
        self.assertEqual(second_page["results"][0]["title"], ordered[1])

    def test_note_aggregate_counts_are_actor_scoped(self) -> None:
        self.graphql(
            """
            mutation {
              login(username: "alice", password: "alice") {
                ok
              }
            }
            """
        )
        data = self.graphql(
            """
            query {
              total: noteAggregate { count }
              byStatus: noteGroups(groupBy: [{field: STATUS}]) {
                totalCount
                results { key { status } count }
              }
              byMonth: noteGroups(
                groupBy: [{field: UPDATED_AT, granularity: MONTH}]
              ) {
                totalCount
                results { key { updatedAtMonth } count }
              }
            }
            """
        )["data"]
        expected_status_counts = {
            str(row["status"]).upper(): row["count"]
            for row in Note.objects.as_user(self.alice).values("status").annotate(count=Count("pk"))
        }
        expected_total = sum(expected_status_counts.values())

        # Ungrouped total is actor-scoped to alice's visible demo rows.
        self.assertGreaterEqual(expected_total, 52)
        self.assertEqual(data["total"]["count"], expected_total)

        # group-by status: groups paginate (totalCount) and carry typed enum
        # keys, matching the schema's NoteStatus group key.
        self.assertEqual(data["byStatus"]["totalCount"], len(expected_status_counts))
        self.assertEqual(
            {row["key"]["status"]: row["count"] for row in data["byStatus"]["results"]},
            expected_status_counts,
        )

        # group-by a date granularity (month) buckets the same scoped rows.
        self.assertGreaterEqual(data["byMonth"]["totalCount"], 1)
        self.assertEqual(
            sum(row["count"] for row in data["byMonth"]["results"]),
            expected_total,
        )
        self.assertTrue(data["byMonth"]["results"][0]["key"]["updatedAtMonth"])

    def test_note_aggregates_accept_the_note_filter_input(self) -> None:
        self.graphql('mutation { login(username: "alice", password: "alice") { ok } }')
        data = self.graphql(
            """
            query {
              total: noteAggregate(filter: {status: {exact: DRAFT}}) {
                count
              }
              byStatus: noteGroups(
                groupBy: [{field: STATUS}],
                filter: {status: {exact: DRAFT}}
              ) {
                totalCount
                results { key { status } count }
              }
            }
            """
        )["data"]
        expected_total = (
            Note.objects.as_user(self.alice)
            .filter(status=Note.Status.DRAFT)
            .count()
        )

        self.assertGreater(expected_total, 0)
        self.assertEqual(data["total"]["count"], expected_total)
        self.assertEqual(data["byStatus"]["totalCount"], 1)
        self.assertEqual(
            data["byStatus"]["results"],
            [{"key": {"status": "DRAFT"}, "count": expected_total}],
        )

    def test_note_group_filter_round_trips_to_scoped_notes_query(self) -> None:
        alice = Client()
        self.login(alice, "alice")

        bucket = self.post(
            alice,
            """
            query {
              noteGroups(
                groupBy: [
                  {field: STATUS},
                  {field: UPDATED_AT, granularity: DAY}
                ],
                pagination: {limit: 1}
              ) {
                results {
                  key {
                    status
                    updatedAtDay
                    updatedAtDayRange { from to }
                  }
                  filter
                  count
                }
              }
            }
            """,
        )["data"]["noteGroups"]["results"][0]

        rows = self.post(
            alice,
            """
            query BucketRows($filter: NoteFilter) {
              notes(filters: $filter, pagination: {limit: 100}) {
                results { status updatedAt }
              }
            }
            """,
            {"filter": bucket["filter"]},
        )["data"]["notes"]["results"]
        start = _parse_datetime(bucket["key"]["updatedAtDayRange"]["from"])
        end = _parse_datetime(bucket["key"]["updatedAtDayRange"]["to"])

        self.assertGreater(len(rows), 0)
        self.assertEqual(
            bucket["filter"],
            {
                "status": {"exact": bucket["key"]["status"]},
                "updatedAt": {
                    "gte": bucket["key"]["updatedAtDayRange"]["from"],
                    "lt": bucket["key"]["updatedAtDayRange"]["to"],
                },
            },
        )
        self.assertTrue(
            all(
                row["status"] == bucket["key"]["status"]
                and start <= _parse_datetime(row["updatedAt"]) < end
                for row in rows
            )
        )

    def test_platform_admin_sees_every_users_notes(self) -> None:
        self.login(self.client, "admin")
        data = self.graphql(
            """
            query {
              notes(pagination: { limit: 100 }) {
                totalCount
                results { createdBy }
              }
            }
            """
        )["data"]["notes"]
        with system_context(reason="test"):
            total = Note.objects.count()

        self.assertEqual(data["totalCount"], total)
        self.assertGreaterEqual(
            {node["createdBy"] for node in data["results"]},
            {
                self.admin.public_id,
                self.alice.public_id,
                self.bob.public_id,
            },
        )

    def test_note_groups_paginate_with_offset(self) -> None:
        self.graphql('mutation { login(username: "alice", password: "alice") { ok } }')
        query = """
            query Page($p: OffsetPaginationInput) {
              noteGroups(groupBy: [{field: STATUS}], pagination: $p) {
                totalCount
                results { key { status } count }
              }
            }
        """
        page0 = self.graphql(query, {"p": {"offset": 0, "limit": 1}})["data"]
        page1 = self.graphql(query, {"p": {"offset": 1, "limit": 1}})["data"]

        # One status group per offset page; totalCount is page-stable and
        # matches the distinct statuses among alice's scoped notes.
        groups = Note.objects.as_user(self.alice).values("status").distinct().count()
        self.assertGreaterEqual(groups, 2)
        self.assertEqual(page0["noteGroups"]["totalCount"], groups)
        self.assertEqual(page1["noteGroups"]["totalCount"], groups)
        self.assertEqual(len(page0["noteGroups"]["results"]), 1)
        self.assertEqual(len(page1["noteGroups"]["results"]), 1)
        first = page0["noteGroups"]["results"][0]["key"]["status"]
        second = page1["noteGroups"]["results"][0]["key"]["status"]
        self.assertNotEqual(first, second)

    def test_owner_gated_flag_is_not_an_aggregate_group_by_axis(self) -> None:
        # ``is_starred`` is an owner-only read (permissions.zed:
        # ``read__is_starred = owner``). It must never be an aggregate group-by
        # axis: grouping runs with field enforcement relaxed, so a reader could
        # otherwise infer another owner's flag from the bucket keys/counts. The
        # schema rejects it as an unknown enum value -- a query-validation
        # error, before execution, so no session is needed.
        payload = self.post(
            Client(),
            """
            query {
              noteGroups(groupBy: [{field: IS_STARRED}]) {
                totalCount
              }
            }
            """,
        )

        self.assertIn("errors", payload)
        self.assertIsNone((payload.get("data") or {}).get("noteGroups"))
        message = payload["errors"][0]["message"].lower()
        self.assertIn("is_starred", message)

    def test_non_owner_cannot_read_another_users_notes(self) -> None:
        alice = Client()
        self.login(alice, "alice")
        alice_notes = self.notes(alice)
        welcome_id = next(node["id"] for node in alice_notes["results"] if node["title"] == "Welcome to Angee")

        bob = Client()
        self.login(bob, "bob")
        bob_notes = self.notes(bob)
        bob_titles = {node["title"] for node in bob_notes["results"]}

        self.assertNotIn("Welcome to Angee", bob_titles)
        self.assertEqual(
            bob_titles,
            {"Draft idea", "Bug triage notes", "1:1 agenda"},
        )

        # bob's scope hides alice's note even when addressed by relay id.
        scoped_out = self.post(
            bob,
            "query NoteByID($id: ID!) { note(id: $id) { id } }",
            {"id": welcome_id},
        )
        self.assertEqual(scoped_out["data"]["note"], None)

    def test_anonymous_mutation_is_denied_with_a_code(self) -> None:
        anonymous = Client()
        payload = self.post(
            anonymous,
            'mutation { createNote(data: {title: "x"}) { id } }',
        )

        self.assertEqual(
            payload["errors"][0]["extensions"]["code"],
            "PERMISSION_DENIED",
        )

    def login(self, client: Client, username: str) -> None:
        """Establish a session for ``username`` with the demo password."""

        result = self.post(
            client,
            """
            mutation Login($username: String!, $password: String!) {
              login(username: $username, password: $password) { ok }
            }
            """,
            {"username": username, "password": username},
        )
        self.assertTrue(result["data"]["login"]["ok"])

    def notes(self, client: Client) -> dict[str, Any]:
        """Return the offset page of notes visible to ``client``."""

        page = self.post(
            client,
            """
            query {
              notes(pagination: { limit: 20 }) {
                totalCount
                results { id title }
              }
            }
            """,
        )
        return cast(dict[str, Any], page["data"]["notes"])

    def graphql(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute one error-free GraphQL operation as ``self.client``."""

        payload = self.post(self.client, query, variables)
        self.assertNotIn("errors", payload)
        return payload

    def post(
        self,
        client: Client,
        query: str,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute one GraphQL operation against the public schema."""

        response = client.post(
            "/graphql/public/",
            data=json.dumps({"query": query, "variables": variables or {}}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        return cast(dict[str, Any], json.loads(response.content))


def _parse_datetime(value: str) -> datetime:
    """Return the datetime encoded by a GraphQL DateTime string."""

    return datetime.fromisoformat(value.replace("Z", "+00:00"))
