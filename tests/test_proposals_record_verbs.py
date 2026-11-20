"""Record contributions use the native optimized proposal schema."""

from pathlib import Path

from graphql import concat_ast, validate

from angee.graphql.schema import GraphQLSchemas
from angee.graphql.testing.documents import authored_documents
from tests.test_proposals_clarifications import CampaignIdentities, ClarificationCase


class RoundRecordTests(CampaignIdentities, ClarificationCase):
    __test__ = False

    def test_active_round_and_authored_documents(self):
        schema = GraphQLSchemas.from_discovery().build("console")
        path = Path(__file__).resolve().parents[1] / "src/angee/proposals/web/src/documents.ts"
        self.assertEqual(validate(schema._schema, concat_ast(authored_documents(path))), [])
        result = self.execute("""query($project: String!) {
          projects_by_pk(id: $project) {
            active_proposal_round { id can_open can_admit roster { user name track_status } }
          }
        }""", {"project": self.project.sqid}, self.manager)
        self.assertIsNone(result.errors, result.errors)
        self.assertEqual(result.data["projects_by_pk"]["active_proposal_round"]["id"], self.round.sqid)
