"""Proposals console projections, checked against the real composed schemas.

Console lists render a user relation through a label-bearing ``UserType``; the public
schema keeps the same relation as a guarded scalar id. The composed host builds every
installed addon, so these are the schemas the console and public clients reach.
"""

from __future__ import annotations

from django.test import SimpleTestCase
from graphql import get_named_type

from angee.graphql.schema import GraphQLSchemas


def _root_node(schema_name: str, root_field: str) -> str:
    """Return the node type a query root field lists."""

    schema = GraphQLSchemas.from_discovery().graphql_schema(schema_name)
    return get_named_type(schema.query_type.fields[root_field].type).name


def _field_type(schema_name: str, type_name: str, field_name: str) -> str:
    """Return the named type of one field on one schema type."""

    schema = GraphQLSchemas.from_discovery().graphql_schema(schema_name)
    return get_named_type(schema.get_type(type_name).fields[field_name].type).name


class ProposalConsoleSchemaTests(SimpleTestCase):
    """Console roots label their users; public roots keep scalar ids."""

    def test_console_rounds_and_reviews_label_their_users(self) -> None:
        """The console facilitator and reviewer resolve to ``UserType``."""

        self.assertEqual(_root_node("console", "proposal_rounds"), "ConsoleProposalRoundType")
        self.assertEqual(_field_type("console", "ConsoleProposalRoundType", "facilitator"), "UserType")
        self.assertEqual(_root_node("console", "proposal_reviews"), "ConsoleProposalReviewType")
        self.assertEqual(_field_type("console", "ConsoleProposalReviewType", "reviewer"), "UserType")
        self.assertEqual(_field_type("console", "ConsoleProposalReviewType", "proposal"), "ConsoleProposalType")

    def test_public_rounds_and_reviews_keep_scalar_ids(self) -> None:
        """The public facilitator and reviewer stay guarded public ids."""

        self.assertEqual(_root_node("public", "proposal_rounds"), "ProposalRoundType")
        self.assertEqual(_field_type("public", "ProposalRoundType", "facilitator"), "ID")
        self.assertEqual(_root_node("public", "proposal_reviews"), "ProposalReviewType")
        self.assertEqual(_field_type("public", "ProposalReviewType", "reviewer"), "ID")
