import { describe, expect, test } from "vitest";
import type { Row } from "@angee/resources";

import { canConnectRecord } from "../connect/ConnectOAuthButton";

describe("integration connect action visibility", () => {
  test("does not show connect for active rows when credential was not selected", () => {
    expect(canConnectRecord({ status: "active" } as Row)).toBe(false);
  });

  test("shows connect for drafts or explicitly empty credentials", () => {
    expect(canConnectRecord({ status: "draft" } as Row)).toBe(true);
    expect(canConnectRecord({ status: "active", credential: null } as Row)).toBe(true);
  });

  test("hides connect for active rows with a credential object", () => {
    expect(
      canConnectRecord({
        status: "active",
        credential: { display_name: "Anthropic Personal Plans" },
      } as unknown as Row),
    ).toBe(false);
  });
});
