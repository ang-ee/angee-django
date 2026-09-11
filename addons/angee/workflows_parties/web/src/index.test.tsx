// @vitest-environment happy-dom

import { expectValidBaseAddon } from "@angee/app/testing";
import { PARTIES_REVIEW_TOOLBAR_SLOT } from "@angee/parties";
import { RecordChromeProvider, formViewSectionsSlot } from "@angee/ui";
import { createRouteHref } from "@angee/ui/runtime";
import { render, screen } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";

const approvalProps = vi.hoisted(() => ({ current: null as Record<string, unknown> | null }));
vi.mock("@angee/workflows", async (importOriginal) => ({
  ...await importOriginal<typeof import("@angee/workflows")>(),
  WorkflowApprovals: (props: Record<string, unknown>) => { approvalProps.current = props; return null; },
}));
vi.mock("@angee/ui", async (importOriginal) => ({
  ...await importOriginal<typeof import("@angee/ui")>(),
  useRouteSearch: () => ({ recordTab: "accounting", decision: "decision-7" }),
}));
vi.mock("@tanstack/react-router", async (importOriginal) => ({
  ...await importOriginal<typeof import("@tanstack/react-router")>(),
  useNavigate: () => vi.fn(),
}));
vi.mock("./i18n", () => ({
  enWorkflowsPartiesMessages: {},
  useWorkflowsPartiesT: () => (key: string) => key,
}));

import workflows from "../../../workflows/web/src/index";
import workflowsParties, { SelectedPartyDecision } from "./index";

describe("workflows-parties addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(workflowsParties)).not.toThrow();
  });

  test("opens the selected Party task over its declared Accounting tab without a nested portal", () => {
    render(
      <RecordChromeProvider value={{ resource: "parties.Organization", canonicalResource: "parties.Party", dataProviderName: "console", recordId: "party-7", record: { id: "party-7" } }}>
        <SelectedPartyDecision />
      </RecordChromeProvider>,
    );

    expect(screen.getByText("Review party details")).toBeTruthy();
    expect(screen.getByText("Review party details").closest("body")).toBe(document.body);
    expect(approvalProps.current).toMatchObject({
      target: { model: "parties.Party", id: "party-7", tab: "accounting" },
      decisionId: "decision-7",
      includeResolved: true,
      selectedTaskOnly: true,
    });
  });

  test("contributes the launcher and one shared activity tab to each canonical party record", () => {
    expect(workflowsParties.routes ?? []).toEqual([]);
    expect((workflowsParties.slots ?? []).map((entry) => [entry.slot, entry.id])).toEqual([
      [PARTIES_REVIEW_TOOLBAR_SLOT, "workflows-parties.dedupe"],
      ["form-view.record-chrome", "workflows-parties.selected-decision"],
      [formViewSectionsSlot("parties.Person").slot, "workflows-parties.activity"],
      [formViewSectionsSlot("parties.Organization").slot, "workflows-parties.activity"],
    ]);
  });

  test("its declared workflows dependency composes the workflows.run target", () => {
    const routeHref = createRouteHref([
      ...(workflows.routes ?? []),
      ...(workflowsParties.routes ?? []),
    ]);

    expect(routeHref("workflows.run", { id: "run 1" })).toBe(
      "/workflows/runs/run%201",
    );
  });
});
