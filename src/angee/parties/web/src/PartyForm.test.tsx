// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

const state = vi.hoisted(() => ({
  kind: "ORGANIZATION" as "ORGANIZATION" | "PERSON" | null,
  fetching: false,
  survivor: "party-1",
}));

vi.mock("@angee/refine", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/refine")>()),
  useAuthoredQuery: () => ({
    data: state.kind ? { party_record: { id: state.survivor, concrete_kind: state.kind } } : { party_record: null },
    isFetching: state.fetching,
  }),
}));
vi.mock("./OrganizationForm", () => ({
  OrganizationForm: (props: { resource: string; id: string; readOnly?: boolean }) =>
    <div data-testid="organization-form">{props.resource}:{props.id}:{String(props.readOnly)}</div>,
}));
vi.mock("./PersonForm", () => ({
  PersonForm: (props: { resource: string; id: string; readOnly?: boolean }) =>
    <div data-testid="person-form">{props.resource}:{props.id}:{String(props.readOnly)}</div>,
}));

import { PartyForm, partyForm } from "./PartyForm";

afterEach(() => {
  cleanup();
  state.kind = "ORGANIZATION";
  state.fetching = false;
  state.survivor = "party-1";
});

test.each([
  ["ORGANIZATION", "organization-form", "parties.Organization"],
  ["PERSON", "person-form", "parties.Person"],
] as const)("delegates a %s parent to its canonical registered form", (kind, testId, resource) => {
  state.kind = kind;
  render(<PartyForm resource="parties.Party" id="party-1" readOnly />);
  expect(screen.getByTestId(testId).textContent).toBe(`${resource}:party-1:true`);
  expect(partyForm.resource).toBe("parties.Party");
});

test("opens a merged-away Party's survivor in its concrete form", () => {
  state.kind = "PERSON";
  state.survivor = "party-2";
  render(<PartyForm resource="parties.Party" id="party-1" readOnly />);
  expect(screen.getByTestId("person-form").textContent).toBe("parties.Person:party-2:true");
});

test("does not invent a form for a missing or non-concrete Party", () => {
  state.kind = null;
  render(<PartyForm resource="parties.Party" id="party-1" readOnly />);
  expect(screen.getByText("Party record unavailable")).toBeTruthy();
  expect(screen.queryByTestId("organization-form")).toBeNull();
  expect(screen.queryByTestId("person-form")).toBeNull();
});

test("keeps the resolved concrete form mounted during a background refetch", () => {
  state.kind = "ORGANIZATION";
  state.fetching = true;
  render(<PartyForm resource="parties.Party" id="party-1" readOnly />);
  expect(screen.getByTestId("organization-form").textContent).toBe("parties.Organization:party-1:true");
  expect(screen.queryByText("Opening party…")).toBeNull();
});

test("shows the loading panel only on the first load, before a Party resolves", () => {
  state.kind = null;
  state.fetching = true;
  render(<PartyForm resource="parties.Party" id="party-1" readOnly />);
  expect(screen.getAllByText("Opening party…").length).toBeGreaterThan(0);
  expect(screen.queryByTestId("organization-form")).toBeNull();
});
