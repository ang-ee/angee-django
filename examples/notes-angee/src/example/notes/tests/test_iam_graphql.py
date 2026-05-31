"""HTTP GraphQL tests for IAM session verbs."""

from __future__ import annotations

import json
from typing import Any, cast

from django.apps import apps
from django.core.management import call_command
from django.test import Client, TransactionTestCase
from rebac import system_context

Note = apps.get_model("notes", "Note")


class IAMGraphQLTests(TransactionTestCase):
    """Drive IAM GraphQL fields through the public HTTP endpoint."""

    def setUp(self) -> None:
        call_command("rebac", "sync", verbosity=0)
        call_command(
            "resources", "load", "demo", allow_non_dev=True, verbosity=0
        )
        with system_context(reason="test-setup"):
            self.welcome = Note.objects.get(title="Welcome to Angee")
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

        current = self.graphql(
            "query { currentUser { username isStaff } }"
        )
        self.assertEqual(
            current["data"],
            {"currentUser": {"username": "alice", "isStaff": True}},
        )

        logged_out = self.graphql("mutation { logout }")
        self.assertEqual(logged_out["data"], {"logout": True})

        anonymous_again = self.graphql(
            "query { currentUser { username } }"
        )
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
        welcome = next(
            node
            for node in notes["results"]
            if node["title"] == "Welcome to Angee"
        )
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

        # Ungrouped total is actor-scoped to alice's three notes.
        self.assertEqual(data["total"]["count"], 3)

        # group-by status: groups paginate (totalCount). The library emits
        # group keys as raw scalars (status: String), so the value is the
        # column value ("active"/"draft"), not the NoteStatus enum member.
        # Enum-typed group keys would be a strawberry-django-aggregates
        # improvement, not an angee workaround.
        self.assertEqual(data["byStatus"]["totalCount"], 2)
        self.assertEqual(
            {
                row["key"]["status"]: row["count"]
                for row in data["byStatus"]["results"]
            },
            {"active": 2, "draft": 1},
        )

        # group-by a date granularity (month) buckets the same three notes.
        self.assertGreaterEqual(data["byMonth"]["totalCount"], 1)
        self.assertEqual(
            sum(row["count"] for row in data["byMonth"]["results"]), 3
        )
        self.assertTrue(data["byMonth"]["results"][0]["key"]["updatedAtMonth"])

    def test_note_groups_paginate_with_offset(self) -> None:
        self.graphql(
            'mutation { login(username: "alice", password: "alice") { ok } }'
        )
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

        # Two status groups, one per offset page; totalCount is page-stable.
        self.assertEqual(page0["noteGroups"]["totalCount"], 2)
        self.assertEqual(len(page0["noteGroups"]["results"]), 1)
        self.assertEqual(len(page1["noteGroups"]["results"]), 1)
        first = page0["noteGroups"]["results"][0]["key"]["status"]
        second = page1["noteGroups"]["results"][0]["key"]["status"]
        self.assertNotEqual(first, second)

    def test_non_owner_cannot_read_another_users_notes(self) -> None:
        alice = Client()
        self.login(alice, "alice")
        alice_notes = self.notes(alice)
        welcome_id = next(
            node["id"]
            for node in alice_notes["results"]
            if node["title"] == "Welcome to Angee"
        )

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
