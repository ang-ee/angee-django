import { defineBaseAddon } from "@angee/app";
import { PARTIES_REVIEW_TOOLBAR_SLOT } from "@angee/parties";

import { RunDedupeAction } from "./RunDedupeAction";
import { enWorkflowsPartiesMessages } from "./i18n";

export { RunDedupeAction } from "./RunDedupeAction";

const workflowsParties = defineBaseAddon({
  id: "workflows-parties",
  i18n: { "workflows-parties": enWorkflowsPartiesMessages },
  // The launcher rides parties' review toolbar; the run and its batch Decision
  // live on the workflows surfaces (run detail + inbox) — one decisions inbox,
  // no parallel review UI here.
  slots: [
    {
      slot: PARTIES_REVIEW_TOOLBAR_SLOT,
      id: "workflows-parties.dedupe",
      sequence: 10,
      content: <RunDedupeAction />,
    },
  ],
});

export default workflowsParties;
