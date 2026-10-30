// @vitest-environment happy-dom

import { render, screen } from "@testing-library/react";
import { moneyWidget } from "@angee/money";
import { AppRuntimeProvider, defaultWidgets } from "@angee/ui";
import { describe, expect, test } from "vitest";

import type {
  ComparisonAnswer,
  ComparisonProposal,
  ComparisonTopic,
} from "./comparison-data";
import { RoundComparisonGrid } from "./comparison-grid";

describe("RoundComparisonGrid", () => {
  test("renders readable columns, aligned markdown answers, money, and empty redactions", async () => {
    const topics: ComparisonTopic[] = [
      {
        id: "topic_delivery",
        key: "delivery",
        name: "Delivery approach",
        sort_order: 10,
      },
    ];
    const proposals: ComparisonProposal[] = [
      {
        id: "proposal_north",
        party: { display_name: "Northstar" },
        responder: "user_north",
        state: "SUBMITTED",
        cost: "72000",
        currency: { code: "EUR" },
      },
      {
        id: "proposal_orbit",
        responder: { id: "user_orbit", display_name: "Orbit" },
        state: "SUBMITTED",
        cost: null,
        currency: null,
      },
    ];
    const answers: ComparisonAnswer[] = [
      {
        id: "answer_north",
        proposal: { id: "proposal_north" },
        topic: { id: "topic_delivery" },
        body: "A **weekly** delivery loop.",
      },
    ];

    render(
      <AppRuntimeProvider
        runtime={{ widgets: { ...defaultWidgets, money: moneyWidget } }}
      >
        <RoundComparisonGrid
          topics={topics}
          proposals={proposals}
          answers={answers}
          facts={["cost"]}
        />
      </AppRuntimeProvider>,
    );

    expect(screen.getByRole("table", { name: "Proposal comparison" })).toBeTruthy();
    expect(screen.getByText("Northstar")).toBeTruthy();
    expect(screen.getByText("Orbit")).toBeTruthy();
    expect(screen.getByText(/€/)).toBeTruthy();
    expect(await screen.findByText("weekly")).toBeTruthy();
    expect(screen.getAllByText("Nothing yet").length).toBeGreaterThan(0);
    expect(screen.queryByRole("rowheader", { name: "Statement" })).toBeNull();
  });
});
