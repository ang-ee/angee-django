// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import type { WorkflowDecisionContentProps } from "@angee/workflows";
import { afterEach, describe, expect, test, vi } from "vitest";

vi.mock("@angee/ui", async (importOriginal) => {
  const { createNamespaceTTestDouble, createUiRouteTestDoubles, createUiTestModule } = await import("@angee/ui/testing");
  return createUiTestModule(importOriginal,
    { createNamespaceT: createNamespaceTTestDouble() },
    createUiRouteTestDoubles(),
  );
});

import { PartyIdentityDecisionContent } from "./PartyIdentityDecisionContent";

afterEach(cleanup);

describe("PartyIdentityDecisionContent", () => {
  test("compares readable identity facts without exposing retained technical IDs", () => {
    render(<PartyIdentityDecisionContent {...decisionProps({
      party_id: "pty_supplier",
      current: {
        name: "Existing Supplier",
        addresses: [{
          id: "adr_private", label: "Billing", street: "Main 1", city: "Prague",
          postal_code: "110 00", country: "CZ", is_primary: true,
        }],
        handles: [{
          id: "phl_private", handle_id: "hdl_private", platform: "email",
          value: "billing@example.com", is_confirmed: true, is_dismissed: false,
        }],
      },
      proposed: {
        name: "Supplier s.r.o.",
        address: { label: "Billing", street: "Main 2", city: "Prague", country: "CZ" },
        handle: { party_handle_id: "phl_private", evidence: "Printed supplier contact" },
      },
      evidence: [{
        label: "Supplier extraction",
        source_model: "workflows_extraction.Extraction",
        source_id: "ext_private",
      }],
    })} />);

    expect(screen.getByText("Current party")).toBeTruthy();
    expect(screen.getByText("Proposed from source")).toBeTruthy();
    expect(screen.getByRole("columnheader", { name: "Current party" })).toBeTruthy();
    expect(screen.getByRole("columnheader", { name: "Proposed from source" })).toBeTruthy();
    expect(screen.getByText("Billing · Main 1 · Prague, 110 00 · CZ")).toBeTruthy();
    expect(screen.getByText("Billing · Main 2 · Prague · CZ")).toBeTruthy();
    expect(screen.getAllByText("billing@example.com")).toHaveLength(2);
    expect(screen.getAllByText("Confirmed").length).toBeGreaterThan(0);
    expect(screen.getByText("Printed supplier contact")).toBeTruthy();
    expect(screen.queryByText("phl_private")).toBeNull();
    expect(screen.queryByText("hdl_private")).toBeNull();
    expect(screen.queryByText("adr_private")).toBeNull();
  });

  test("does not infer a proposed contact when the proposal has no value or link", () => {
    render(<PartyIdentityDecisionContent {...decisionProps({
      party_id: "pty_supplier",
      current: {
        name: "Existing Supplier",
        addresses: [],
        handles: [{
          id: "phl_current", platform: "email", value: "current@example.com",
          is_confirmed: false, is_dismissed: true,
        }],
      },
      proposed: {
        name: "Existing Supplier",
        address: {},
        handle: { party_handle_id: "", evidence: "" },
      },
      evidence: [],
    })} />);

    expect(screen.getByText("current@example.com")).toBeTruthy();
    expect(screen.getAllByText("Not provided").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("Dismissed")).toBeTruthy();
  });
});

function decisionProps(payload: Record<string, unknown>): WorkflowDecisionContentProps {
  return {
    approval: { id: "wdc_identity", payload } as WorkflowDecisionContentProps["approval"],
    contextFields: [], contextValues: { review_context: payload }, inputFields: [], values: {},
    setValue: vi.fn(), selectAction: vi.fn(), messagesFor: () => [],
    editable: true, fetching: false, readOnly: false,
  };
}
