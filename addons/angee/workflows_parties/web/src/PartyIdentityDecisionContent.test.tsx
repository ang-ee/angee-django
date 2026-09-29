// @vitest-environment happy-dom
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";

import { PartyIdentityDecisionContent } from "./PartyIdentityDecisionContent";

afterEach(cleanup);

test("compares retained names, native addresses and the proposed contact with its status and evidence", () => {
  render(<PartyIdentityDecisionContent basis={{
    party_id: "pty_sample",
    current: { name: "Earlier name", addresses: [{ street: "10 First Street", city: "Prague" }], handles: [
      { id: "phd_sample", platform: "email", value: "hello@example.test", is_confirmed: false, is_dismissed: true },
    ] },
    proposed: { name: "Updated name", address: { street: "20 Second Street", city: "Brno" }, handle: { party_handle_id: "phd_sample", evidence: "A retained directory entry" } },
  }} />);
  expect(screen.getByRole("columnheader", { name: "Current" })).toBeTruthy();
  expect(screen.getByRole("columnheader", { name: "Proposed" })).toBeTruthy();
  expect(screen.getByText("Earlier name")).toBeTruthy();
  expect(screen.getByText("Updated name")).toBeTruthy();
  expect(screen.getByText("10 First Street · Prague")).toBeTruthy();
  expect(screen.getByText("20 Second Street · Brno")).toBeTruthy();
  expect(within(screen.getByRole("list", { name: "Current contacts" })).getByText("Dismissed")).toBeTruthy();
  expect(screen.getAllByText("hello@example.test")).toHaveLength(2);
  expect(screen.getByText("A retained directory entry")).toBeTruthy();
});

test("renders missing optional identity values without inventing a contact", () => {
  render(<PartyIdentityDecisionContent basis={{ party_id: "pty_empty", current: { name: "", addresses: [], handles: [] }, proposed: { name: "", address: {}, handle: { party_handle_id: "", evidence: "" } } }} />);
  expect(screen.getAllByText("Not recorded")).toHaveLength(3);
  expect(screen.getAllByText("Not provided")).toHaveLength(3);
});

test.each([null, {}, { current: { name: 12 } }])("invalid retained identity data renders an explicit unavailable state", (basis) => {
  render(<PartyIdentityDecisionContent basis={basis} />);
  expect(screen.getByText("Review details unavailable")).toBeTruthy();
  expect(screen.queryByRole("table")).toBeNull();
});
