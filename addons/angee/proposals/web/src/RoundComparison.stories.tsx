import { moneyWidget } from "@angee/money";
import {
  AppRuntimeProvider,
  Page,
  PageBody,
  PageHeader,
  defaultWidgets,
} from "@angee/ui";
import type { ReactElement, ReactNode } from "react";

import type {
  ComparisonAnswer,
  ComparisonProposal,
  ComparisonRound,
  ComparisonTopic,
} from "./comparison-data";
import { RoundComparisonGrid } from "./views/RoundComparisonPage";

const round: ComparisonRound = {
  id: "rnd_competitive",
  name: "Customer portal build",
  status: "OPENED",
  opening_policy: "ANSWERS",
};

const topics: ComparisonTopic[] = [
  {
    id: "top_approach",
    key: "approach",
    name: "Delivery approach",
    hint: "Explain the milestones and feedback loop.",
    sort_order: 10,
  },
  {
    id: "top_risk",
    key: "risk",
    name: "Primary risk",
    hint: "Name the largest delivery uncertainty.",
    sort_order: 20,
  },
];

const proposals: ComparisonProposal[] = [
  {
    id: "prp_north",
    responder: "user_north",
    party: { display_name: "Northstar Studio" },
    state: "SUBMITTED",
    cost: "72000",
    currency: { code: "EUR" },
    staffing: "1 lead, 2 engineers",
    timeframe_start: "2026-09-14",
    timeframe_end: "2026-12-18",
    confidence: "HIGH",
    valid_until: "2026-09-05",
  },
  {
    id: "prp_orbit",
    responder: "user_orbit",
    party: { display_name: "Orbit Works" },
    state: "SUBMITTED",
    cost: "64000",
    currency: { code: "EUR" },
    staffing: "2 senior engineers",
    timeframe_start: "2026-09-21",
    timeframe_end: "2027-01-15",
    confidence: "MEDIUM",
    valid_until: null,
  },
  {
    id: "prp_field",
    responder: "user_field",
    party: { display_name: "Field Office" },
    state: "SUBMITTED",
    cost: null,
    currency: null,
    staffing: null,
    timeframe_start: "2026-10-01",
    timeframe_end: "2026-12-22",
    confidence: null,
    valid_until: "2026-09-10",
  },
];

const answers: ComparisonAnswer[] = [
  {
    id: "ans_north_approach",
    proposal: { id: "prp_north" },
    topic: { id: "top_approach" },
    body: "Weekly increments with a **fortnightly stakeholder review**.",
  },
  {
    id: "ans_north_risk",
    proposal: { id: "prp_north" },
    topic: { id: "top_risk" },
    body: "Identity-provider migration and test data quality.",
  },
  {
    id: "ans_orbit_approach",
    proposal: { id: "prp_orbit" },
    topic: { id: "top_approach" },
    body: "A discovery sprint followed by three production releases.",
  },
  {
    id: "ans_orbit_risk",
    proposal: { id: "prp_orbit" },
    topic: { id: "top_risk" },
    body: "Availability of customer-support subject-matter experts.",
  },
  {
    id: "ans_field_approach",
    proposal: { id: "prp_field" },
    topic: { id: "top_approach" },
    body: "Prototype first, then scope the implementation from measured usage.",
  },
];

const responderProposalId = "prp_north";
const responderPreOpenRound: ComparisonRound = {
  ...round,
  status: "COLLECTING",
};
const responderPreOpenProposals = proposals.filter(
  (proposal) => proposal.id === responderProposalId,
);
const responderPreOpenAnswers = answers.filter(
  (answer) => answer.proposal?.id === responderProposalId,
);

const meta = {
  title: "Proposals/Round comparison",
  parameters: { layout: "fullscreen" },
};

export default meta;

function RuntimeFixture({ children }: { children: ReactNode }): ReactElement {
  return (
    <AppRuntimeProvider
      runtime={{ widgets: { ...defaultWidgets, money: moneyWidget } }}
    >
      {children}
    </AppRuntimeProvider>
  );
}

export const CompetitiveRound = {
  render: () => (
    <RuntimeFixture>
      <Page className="h-screen">
        <PageHeader
          title={`Compare ${String(round.name)}`}
          description="Competitive round fixture: three readable columns, aligned topics, and intentionally empty offer facts."
        />
        <PageBody gutter="none">
          <RoundComparisonGrid
            topics={topics}
            proposals={proposals}
            answers={answers}
          />
        </PageBody>
      </Page>
    </RuntimeFixture>
  ),
};

export const ResponderPreOpen = {
  render: () => (
    <RuntimeFixture>
      <Page className="h-screen">
        <PageHeader
          title={`Compare ${String(responderPreOpenRound.name)}`}
          description="Responder pre-open fixture: the viewer's own proposal is the only readable column; every other proposal is absent."
        />
        <PageBody gutter="none">
          <RoundComparisonGrid
            topics={topics}
            proposals={responderPreOpenProposals}
            answers={responderPreOpenAnswers}
          />
        </PageBody>
      </Page>
    </RuntimeFixture>
  ),
};
