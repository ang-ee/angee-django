import { defineBaseAddon } from "@angee/app";
import { decisionContent } from "@angee/decisions";

import { DuplicatePartyDecisionContent } from "./DuplicatePartyDecisionContent";
import { PartyIdentityDecisionContent } from "./PartyIdentityDecisionContent";
import { enWorkflowsPartiesMessages } from "./i18n";

export default defineBaseAddon({
  id: "workflows-parties",
  i18n: { "workflows-parties": enWorkflowsPartiesMessages },
  containers: {
    "decisions#content": {
      "workflows-parties.review-party-identity": decisionContent("review-party-identity", PartyIdentityDecisionContent),
      "workflows-parties.review-dupe-party": decisionContent("review-dupe-party", DuplicatePartyDecisionContent),
    },
  },
});
