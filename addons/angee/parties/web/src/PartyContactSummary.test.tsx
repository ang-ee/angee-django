// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

vi.mock("./i18n", () => ({ usePartiesT: () => (key: string) => key }));

import {
  PartyContactSummary,
  partyAddressText,
  partyContactValues,
} from "./PartyContactSummary";

afterEach(cleanup);

describe("PartyContactSummary", () => {
  const party = {
    display_name: "Example Organization",
    addresses: [
      { label: "Old", street: "1 Previous St", city: "Miami", country: "US" },
      {
        label: "Contact", street: "151 Main St", extended: "Suite 8",
        city: "San Juan", region: "PR", postal_code: "00901", country: "US", is_primary: true,
      },
    ],
    handles: [
      { platform: "email", value: "old@example.test" },
      { platform: "email", value: "contact@example.test", is_preferred: true },
      { platform: "phone", value: "+1 555 0100", is_preferred: true },
    ],
  };

  test("formats an address and preferred contact values", () => {
    expect(partyAddressText(party.addresses[1])).toBe(
      "Contact · 151 Main St · Suite 8 · San Juan, PR, 00901 · US",
    );
    expect(partyContactValues(party)).toEqual({
      email: "contact@example.test",
      phone: "+1 555 0100",
    });
  });

  test("renders the loaded party identity without another data owner", () => {
    render(<PartyContactSummary party={party} />);
    expect(screen.getByRole("region", { name: "party.contact.summary" })).toBeTruthy();
    expect(screen.getByText("Example Organization")).toBeTruthy();
    expect(screen.getByText(/151 Main St/)).toBeTruthy();
    expect(screen.getByText("contact@example.test")).toBeTruthy();
    expect(screen.getByText("+1 555 0100")).toBeTruthy();
  });
});
