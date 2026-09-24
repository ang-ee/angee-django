// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import type { WorkflowDecisionContentProps } from "@angee/workflows";
import { afterEach, describe, expect, test, vi } from "vitest";

vi.mock("@angee/ui", async (importOriginal) => {
  const { createUiRouteTestDoubles, createUiTestModule } = await import("@angee/ui/testing");
  return createUiTestModule(importOriginal,
    createUiRouteTestDoubles(),
  );
});

import { PartyIdentityDecisionContent } from "./PartyIdentityDecisionContent";

afterEach(cleanup);

describe("PartyIdentityDecisionContent", () => {
  test("compares readable identity facts without exposing retained technical IDs", () => {
    render(<PartyIdentityDecisionContent {...decisionProps({
      current: {
        name: "Existing Counterparty",
        addresses: [{
          id: "adr_private", label: "Contact", street: "Main 1", city: "Prague",
          postal_code: "110 00", country: "CZ", is_primary: true,
        }],
        handles: [{
          id: "phl_private", handle_id: "hdl_private", platform: "email",
          value: "contact@example.com", is_confirmed: true, is_dismissed: false,
        }],
      },
      proposed: {
        name: "Counterparty s.r.o.",
        address: { label: "Contact", street: "Main 2", city: "Prague", country: "CZ" },
        handle: { party_handle_id: "phl_private", evidence: "Printed counterparty contact" },
      },
      evidence: [{
        label: "Counterparty extraction",
        model: "workflows_extraction.Extraction",
        id: "ext_private",
      }],
    })} />);

    expect(screen.getByText("Current party")).toBeTruthy();
    expect(screen.getByText("Proposed from source")).toBeTruthy();
    expect(screen.getByRole("columnheader", { name: "Current party" })).toBeTruthy();
    expect(screen.getByRole("columnheader", { name: "Proposed from source" })).toBeTruthy();
    expect(screen.getByText("Contact · Main 1 · Prague, 110 00 · CZ")).toBeTruthy();
    expect(screen.getByText("Contact · Main 2 · Prague · CZ")).toBeTruthy();
    expect(screen.getAllByText("contact@example.com")).toHaveLength(2);
    expect(screen.getAllByText("Confirmed").length).toBeGreaterThan(0);
    expect(screen.getByText("Printed counterparty contact")).toBeTruthy();
    expect(screen.queryByText("phl_private")).toBeNull();
    expect(screen.queryByText("hdl_private")).toBeNull();
    expect(screen.queryByText("adr_private")).toBeNull();
  });

  test("does not infer a proposed contact when the proposal has no value or link", () => {
    render(<PartyIdentityDecisionContent {...decisionProps({
      current: {
        name: "Existing Counterparty",
        addresses: [],
        handles: [{
          id: "phl_current", platform: "email", value: "current@example.com",
          is_confirmed: false, is_dismissed: true,
        }],
      },
      proposed: {
        name: "Existing Counterparty",
        address: {},
        handle: { party_handle_id: "", evidence: "" },
      },
      evidence: [],
    })} />);

    expect(screen.getByText("current@example.com")).toBeTruthy();
    expect(screen.getAllByText("Not provided").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("Dismissed")).toBeTruthy();
  });

  test("keeps native actions available when retained identity facts are incomplete", () => {
    render(<PartyIdentityDecisionContent {...decisionProps({
      current: { name: "Existing Counterparty", addresses: [], handles: [] },
      proposed: undefined,
      evidence: [],
    })} />);

    expect(screen.getByText("Review context unavailable")).toBeTruthy();
    expect(screen.getByText("Native identity actions")).toBeTruthy();
  });
});

function decisionProps({ current, proposed, evidence }: {
  current: Record<string, unknown>;
  proposed?: Record<string, unknown>;
  evidence: Array<{ label: string; model: string; id: string }>;
}): WorkflowDecisionContentProps {
  const facts = [{
    pointer: "/current",
    label: "Current Party identity",
    value: current,
    subject: { model: "parties.Party", id: "pty_counterparty", label: "Current Party" },
    authority: "source",
    evidence: [],
  }, ...(proposed === undefined ? [] : [{
    pointer: "/proposed",
    label: "Proposed Party identity",
    value: proposed,
    authority: "unverified",
    evidence,
  }])];
  return {
    approval: { id: "wdc_identity", payload: { facts } } as unknown as WorkflowDecisionContentProps["approval"],
    contextFields: [], contextValues: { facts }, inputFields: [], values: {},
    setValue: vi.fn(), selectAction: vi.fn(), messagesFor: () => [],
    actionPicker: <span>Native identity actions</span>,
    editable: true, fetching: false, readOnly: false,
  };
}
