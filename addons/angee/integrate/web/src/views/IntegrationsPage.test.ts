import { describe, expect, test } from "vitest";
import type { Row } from "@angee/sdk";

import { canConnectIntegration } from "./IntegrationsPage";

describe("integration connect action visibility", () => {
  test("does not show connect for active rows when credential was not selected", () => {
    expect(canConnectIntegration({ status: "active" } as Row)).toBe(false);
  });

  test("shows connect for drafts or explicitly empty credentials", () => {
    expect(canConnectIntegration({ status: "draft" } as Row)).toBe(true);
    expect(canConnectIntegration({ status: "active", credential: null } as Row)).toBe(true);
  });

  test("hides connect for active rows with a credential object", () => {
    expect(
      canConnectIntegration({
        status: "active",
        credential: { displayName: "Anthropic Personal Plans" },
      } as unknown as Row),
    ).toBe(false);
  });
});
