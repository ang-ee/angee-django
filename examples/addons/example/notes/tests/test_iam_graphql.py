"""HTTP GraphQL tests for IAM session verbs."""

from __future__ import annotations

import json
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
        anonymous = self.graphql("query { current_user { username } }")
        self.assertEqual(anonymous["data"], {"current_user": None})

        logged_in = self.graphql(
            """
            mutation {
              login(username: "alice", password: "alice") {
                ok
                user { username is_staff }
              }
            }
            """
        )

        self.assertEqual(
            logged_in["data"],
            {
                "login": {
                    "ok": True,
                    "user": {"username": "alice", "is_staff": True},
                }
            },
        )

        current = self.graphql("query { current_user { username is_staff } }")
        self.assertEqual(
            current["data"],
            {"current_user": {"username": "alice", "is_staff": True}},
        )

        logged_out = self.graphql("mutation { logout }")
        self.assertEqual(logged_out["data"], {"logout": True})

        anonymous_again = self.graphql("query { current_user { username } }")
        self.assertEqual(anonymous_again["data"], {"current_user": None})

    def test_notes_page_and_update_by_public_id(self) -> None:
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
              notes(limit: 10) {
                id
                title
                tags
                word_count
              }
              notes_aggregate { aggregate { count } }
            }
            """
        )

        notes = page["data"]["notes"]
        self.assertGreaterEqual(page["data"]["notes_aggregate"]["aggregate"]["count"], 3)
        welcome = next(node for node in notes if node["title"] == "Welcome to Angee")
        public_id = welcome["id"]
        self.assertEqual(public_id, self.welcome.sqid)
        self.assertIn("backend", welcome["tags"])
        self.assertGreater(welcome["word_count"], 0)

        by_id = self.graphql(
            """
            query NoteByID($id: String!) {
              notes_by_pk(id: $id) { id title }
            }
            """,
            {"id": public_id},
        )
        self.assertEqual(
            by_id["data"]["notes_by_pk"],
            {"id": public_id, "title": "Welcome to Angee"},
        )

        updated = self.graphql(
            """
            mutation UpdateNote($id: String!) {
              update_notes_by_pk(
                pk_columns: {id: $id},
                _set: {title: "Welcome through Public ID"}
              ) {
                id
                title
              }
            }
            """,
            {"id": public_id},
        )
        self.assertEqual(
            updated["data"]["update_notes_by_pk"],
            {"id": public_id, "title": "Welcome through Public ID"},
        )

    def test_note_can_have_in_review_status(self) -> None:
        alice = Client()
        self.login(alice, "alice")
        page = self.post(
            alice,
            """
            query {
              notes(limit: 20) { title status }
            }
            """,
        )["data"]["notes"]

        planning = next(node for node in page if node["title"] == "Quarterly planning")
        self.assertEqual(planning["status"], "IN_REVIEW")

    def test_note_exposes_scalar_audit_ids_and_stamps_updates(self) -> None:
        alice = Client()
        self.login(alice, "alice")
        alice_notes = self.post(
            alice,
            """
            query {
              notes(limit: 20) { id title created_by updated_by }
            }
            """,
        )["data"]["notes"]
        planning = next(node for node in alice_notes if node["title"] == "Quarterly planning")

        self.assertEqual(planning["created_by"], self.alice.public_id)
        self.assertEqual(planning["updated_by"], self.admin.public_id)
        self.assertIsInstance(planning["created_by"], str)
        self.assertIsInstance(planning["updated_by"], str)
        self.assertTrue(planning["created_by"])
        self.assertTrue(planning["updated_by"])

        updated = self.post(
            alice,
            """
            mutation UpdateNote($id: String!) {
              update_notes_by_pk(
                pk_columns: {id: $id},
                _set: {title: "Quarterly planning updated"}
              ) {
                id
                title
                created_by
                updated_by
              }
            }
            """,
            {"id": planning["id"]},
        )

        self.assertEqual(
            updated["data"]["update_notes_by_pk"],
            {
                "id": planning["id"],
                "title": "Quarterly planning updated",
                "created_by": self.alice.public_id,
                "updated_by": self.alice.public_id,
            },
        )

    def test_update_preserves_redacted_fields_for_validation(self) -> None:
        with system_context(reason="test-setup"):
            note = Note.objects.create(
                title="Admin update target",
                body="owner-only flag",
                created_by=self.alice,
                is_starred=True,
            )
            sqid = note.sqid

        self.assertIsNone(Note.objects.as_user(self.admin).get(sqid=sqid).is_starred)
        self.login(self.client, "admin")
        notes = self.graphql(
            """
            query {
              notes(limit: 100) { id title }
            }
            """
        )["data"]["notes"]
        target = next(node for node in notes if node["title"] == "Admin update target")

        updated = self.graphql(
            """
            mutation UpdateNote($id: String!) {
              update_notes_by_pk(
                pk_columns: {id: $id},
                _set: {title: "Admin updated target"}
              ) {
                id
                title
              }
            }
            """,
            {"id": target["id"]},
        )

        self.assertEqual(
            updated["data"]["update_notes_by_pk"],
            {
                "id": target["id"],
                "title": "Admin updated target",
            },
        )
        with system_context(reason="test"):
            note = Note.objects.get(sqid=sqid)
            self.assertEqual(note.title, "Admin updated target")
            self.assertTrue(note.is_starred)

    def test_note_audit_labels_are_strings_without_user_projection(self) -> None:
        self.login(self.client, "admin")
        notes = self.graphql(
            """
            query {
              notes(limit: 100) {
                title
                created_by
                created_by_label
                updated_by
                updated_by_label
              }
            }
            """
        )["data"]["notes"]
        named = next(node for node in notes if node["title"] == self.named_note.title)
        planning = next(node for node in notes if node["title"] == "Quarterly planning")

        self.assertEqual(named["created_by"], self.named_owner.public_id)
        self.assertEqual(named["created_by_label"], "Named Owner")
        self.assertEqual(named["updated_by"], self.bob.public_id)
        self.assertEqual(named["updated_by_label"], "bob")
        self.assertEqual(planning["created_by"], self.alice.public_id)
        self.assertEqual(planning["created_by_label"], "alice")
        self.assertEqual(planning["updated_by"], self.admin.public_id)
        self.assertEqual(planning["updated_by_label"], "admin")

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

        self.assertEqual(fields["created_by"], {"kind": "SCALAR", "name": "ID"})
        self.assertEqual(fields["updated_by"], {"kind": "SCALAR", "name": "ID"})
        self.assertEqual(fields["created_by_label"], {"kind": "SCALAR", "name": "String"})
        self.assertEqual(fields["updated_by_label"], {"kind": "SCALAR", "name": "String"})
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
              note_revisions(id: $id) {
                id
                created_at
                comment
                body
              }
            }
            """,
            {"id": welcome_id},
        )

        revisions = visible["data"]["note_revisions"]
        self.assertEqual(
            [revision["body"] for revision in revisions],
            ["Second reviewed body", "First reviewed body"],
        )
        self.assertEqual(
            [revision["comment"] for revision in revisions],
            ["second body", "first body"],
        )
        self.assertTrue(all(revision["id"] for revision in revisions))
        self.assertTrue(all(revision["created_at"] for revision in revisions))

        bob = Client()
        self.login(bob, "bob")
        scoped_out = self.post(
            bob,
            """
            query NoteRevisions($id: ID!) {
              note_revisions(id: $id) { id }
            }
            """,
            {"id": welcome_id},
        )
        self.assertEqual(scoped_out["data"]["note_revisions"], [])

    def test_notes_paginate_in_declared_order(self) -> None:
        alice = Client()
        self.login(alice, "alice")
        full = self.notes(alice)
        ordered = [node["title"] for node in full["results"]]

        first_page = self.post(
            alice,
            """
            query {
              notes(offset: 0, limit: 1) { title }
              notes_aggregate { aggregate { count } }
            }
            """,
        )["data"]
        second_page = self.post(
            alice,
            """
            query {
              notes(offset: 1, limit: 1) { title }
            }
            """,
        )["data"]["notes"]

        # Meta.ordering is ("-updated_at", "title", "sqid"); offset pages
        # follow that order without overlap.
        self.assertEqual(first_page["notes"][0]["title"], ordered[0])
        self.assertGreater(first_page["notes_aggregate"]["aggregate"]["count"], 1)
        self.assertEqual(second_page[0]["title"], ordered[1])

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
              total: notes_aggregate { aggregate { count } }
              byStatus: notes_groups(group_by: [{field: STATUS}]) {
                key { status }
                aggregate { count }
              }
              byMonth: notes_groups(group_by: [{field: UPDATED_AT, granularity: MONTH}]) {
                key { updated_at_month updated_at_month_range { from to } }
                aggregate { count }
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
        self.assertEqual(data["total"]["aggregate"]["count"], expected_total)

        # group-by status carries typed keys and per-group aggregate values.
        self.assertEqual(len(data["byStatus"]), len(expected_status_counts))
        self.assertEqual(
            {
                _dimension(row, "status"): row["aggregate"]["count"]
                for row in data["byStatus"]
            },
            expected_status_counts,
        )

        # group-by a date granularity (month) buckets the same scoped rows.
        self.assertGreaterEqual(len(data["byMonth"]), 1)
        self.assertEqual(
            sum(row["aggregate"]["count"] for row in data["byMonth"]),
            expected_total,
        )
        self.assertTrue(_dimension(data["byMonth"][0], "updated_at_month"))

    def test_note_aggregates_accept_the_note_filter_input(self) -> None:
        self.graphql('mutation { login(username: "alice", password: "alice") { ok } }')
        data = self.graphql(
            """
            query {
              total: notes_aggregate(where: {status: {_eq: "draft"}}) {
                aggregate { count }
              }
              byStatus: notes_groups(
                group_by: [{field: STATUS}],
                where: {status: {_eq: "draft"}}
              ) {
                key { status }
                aggregate { count }
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
        self.assertEqual(data["total"]["aggregate"]["count"], expected_total)
        self.assertEqual(len(data["byStatus"]), 1)
        self.assertEqual(_dimension(data["byStatus"][0], "status"), "DRAFT")
        self.assertEqual(data["byStatus"][0]["aggregate"]["count"], expected_total)

    def test_platform_admin_sees_every_users_notes(self) -> None:
        self.login(self.client, "admin")
        data = self.graphql(
            """
            query {
              notes(limit: 100) { created_by }
              notes_aggregate { aggregate { count } }
            }
            """
        )["data"]
        with system_context(reason="test"):
            total = Note.objects.count()

        self.assertEqual(data["notes_aggregate"]["aggregate"]["count"], total)
        self.assertGreaterEqual(
            {node["created_by"] for node in data["notes"]},
            {
                self.admin.public_id,
                self.alice.public_id,
                self.bob.public_id,
            },
        )

    def test_note_groups_paginate_with_offset(self) -> None:
        self.graphql('mutation { login(username: "alice", password: "alice") { ok } }')
        query = """
            query Page($limit: Int!, $offset: Int!) {
              notes_groups(group_by: [{field: STATUS}], limit: $limit, offset: $offset) {
                key { status }
                aggregate { count }
              }
            }
        """
        page0 = self.graphql(query, {"offset": 0, "limit": 1})["data"]
        page1 = self.graphql(query, {"offset": 1, "limit": 1})["data"]

        # One status group per offset page; the distinct status count is owned
        # by the aggregate queryset and the groups root pages over that list.
        groups = Note.objects.as_user(self.alice).values("status").distinct().count()
        self.assertGreaterEqual(groups, 2)
        self.assertEqual(len(page0["notes_groups"]), 1)
        self.assertEqual(len(page1["notes_groups"]), 1)
        first = _dimension(page0["notes_groups"][0], "status")
        second = _dimension(page1["notes_groups"][0], "status")
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
              notes_groups(group_by: [{field: IS_STARRED}]) { aggregate { count } }
            }
            """,
        )

        self.assertIn("errors", payload)
        self.assertIsNone((payload.get("data") or {}).get("notes_groups"))
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

        # bob's scope hides alice's note even when addressed by public id.
        scoped_out = self.post(
            bob,
            "query NoteByID($id: String!) { notes_by_pk(id: $id) { id } }",
            {"id": welcome_id},
        )
        self.assertEqual(scoped_out["data"]["notes_by_pk"], None)

    def test_anonymous_mutation_is_denied_with_a_code(self) -> None:
        anonymous = Client()
        payload = self.post(
            anonymous,
            'mutation { insert_notes_one(object: {title: "x"}) { id } }',
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
              notes(limit: 20) { id title }
              notes_aggregate { aggregate { count } }
            }
            """,
        )
        return {
            "totalCount": page["data"]["notes_aggregate"]["aggregate"]["count"],
            "results": page["data"]["notes"],
        }

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

def _dimension(group: dict[str, Any], key: str) -> str:
    """Return a named typed group key value."""

    return cast(str, group["key"][key])
