"""Record contributions use the native optimized proposal schema."""

import re
from pathlib import Path

from graphql import parse, validate

from angee.graphql.schema import GraphQLSchemas
from tests.composed_host import run_composed_tests
from tests.test_proposals_campaign_composed import CampaignIdentities
from tests.test_proposals_clarifications import ClarificationCase


class RoundRecordTests(CampaignIdentities, ClarificationCase):
    __test__ = False

    def test_active_round_and_authored_documents(self):
        schema = GraphQLSchemas.from_discovery().build("console")
        path = Path(__file__).resolve().parents[1] / "addons/angee/proposals/web/src/documents.ts"
        document = parse("\n".join(re.findall(r"graphql\(`(.*?)`\)", path.read_text(), flags=re.S)))
        self.assertEqual(validate(schema._schema, document), [])
        result = self.execute("""query($project: String!) {
          projects_by_pk(id: $project) {
            active_proposal_round { id can_open can_admit roster { user name track_status } }
          }
        }""", {"project": self.project.sqid}, self.manager)
        self.assertIsNone(result.errors, result.errors)
        self.assertEqual(result.data["projects_by_pk"]["active_proposal_round"]["id"], self.round.sqid)


def test_round_record_verbs(tmp_path: Path) -> None:
    run_composed_tests(
        tmp_path, "tests.test_proposals_record_verbs.RoundRecordTests.test_active_round_and_authored_documents",
        app="angee.proposals",
    )
