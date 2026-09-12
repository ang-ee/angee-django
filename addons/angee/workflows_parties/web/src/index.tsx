import { defineBaseAddon } from "@angee/app";
import { PARTIES_REVIEW_TOOLBAR_SLOT } from "@angee/parties";
import {
  DialogBackdrop,
  DialogContent,
  DialogPortal,
  DialogRoot,
  DialogTitle,
  FORM_VIEW_RECORD_CHROME_SLOT,
  RECORD_TAB_SEARCH_KEY,
  Tab,
  formViewSectionsSlot,
  routeSearchParam,
  useRecordChromeContext,
  useRouteSearch,
} from "@angee/ui";
import { DECISION_SEARCH_KEY, decisionSearch, WorkflowApprovals } from "@angee/workflows";
import { useNavigate } from "@tanstack/react-router";
import type { ReactElement } from "react";

import { RunDedupeAction } from "./RunDedupeAction";
import { enWorkflowsPartiesMessages, useWorkflowsPartiesT } from "./i18n";

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
    {
      slot: FORM_VIEW_RECORD_CHROME_SLOT,
      id: "workflows-parties.selected-decision",
      sequence: 40,
      content: <SelectedPartyDecision />,
    },
    ...["parties.Person", "parties.Organization"].map((model) => ({
      ...formViewSectionsSlot(model),
      id: "workflows-parties.activity",
      sequence: 90,
      content: (
        <Tab id="workflow-activity" label={<WorkflowActivityLabel />}>
          <PartyWorkflowActivity />
        </Tab>
      ),
    })),
  ],
});

function WorkflowActivityLabel(): ReactElement {
  const t = useWorkflowsPartiesT();
  return <>{t("activity.label")}</>;
}

function PartyWorkflowActivity(): ReactElement {
  const record = useRecordChromeContext();
  const search = useRouteSearch();
  const navigate = useNavigate();
  const decisionId = routeSearchParam(search, DECISION_SEARCH_KEY) ?? null;
  return (
    <WorkflowApprovals
      target={{ model: "parties.Party", id: record.recordId }}
      includeResolved
      decisionId={decisionId}
      onDecisionChange={(decision) => {
        void navigate({
          to: ".",
          replace: true,
          search: (previous: Record<string, unknown>) => decisionSearch(previous, decision),
        });
      }}
    />
  );
}

export function SelectedPartyDecision(): ReactElement | null {
  const record = useRecordChromeContext();
  const search = useRouteSearch();
  const navigate = useNavigate();
  const tab = routeSearchParam(search, RECORD_TAB_SEARCH_KEY);
  const decisionId = routeSearchParam(search, DECISION_SEARCH_KEY);
  if ((record.resource !== "parties.Person" && record.resource !== "parties.Organization")
      || !tab || tab === "workflow-activity" || !decisionId) return null;
  const clearDecision = (): void => {
    void navigate({
      to: ".",
      replace: true,
      search: (previous: Record<string, unknown>) => decisionSearch(previous, null),
    });
  };
  return (
    <DialogRoot open onOpenChange={(open) => { if (!open) clearDecision(); }}>
      <DialogPortal>
        <DialogBackdrop />
        <DialogContent size="lg" className="h-[min(48rem,calc(100vh-2rem))] overflow-hidden p-0">
          <div className="flex h-full min-h-0 flex-col">
            <DialogTitle className="border-b border-border px-4 py-3">Review party details</DialogTitle>
            <div className="min-h-0 flex-1">
              <WorkflowApprovals
                target={{ model: "parties.Party", id: record.recordId, tab }}
                includeResolved
                selectedTaskOnly
                decisionId={decisionId}
                onDecisionChange={(next) => { if (!next) clearDecision(); }}
              />
            </div>
          </div>
        </DialogContent>
      </DialogPortal>
    </DialogRoot>
  );
}

export default workflowsParties;
