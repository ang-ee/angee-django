import { describe, expect, test } from "vitest";
import type { Row } from "@angee/metadata";

import { canConnectRecord } from "../connect/ConnectOAuthButton";
import { concreteIntegrationHref } from "./IntegrationsPage";

describe("integration connect action visibility", () => {
  test("shows connect only for a row with no credential to connect with", () => {
    expect(canConnectRecord({ lifecycle: "connected", credential: null } as Row)).toBe(true);
    expect(canConnectRecord({ lifecycle: "disconnected", credential: null } as Row)).toBe(true);
  });

  test("hides connect for a row that still holds a credential", () => {
    expect(
      canConnectRecord({
        lifecycle: "connected",
        credential: { display_name: "Anthropic Personal Plans" },
      } as unknown as Row),
    ).toBe(false);
    // A *disconnected* row that kept its credential reconnects through Resume
    // (`Integration.connect` declares `source=[DISCONNECTED, PAUSED]`), not through
    // a fresh OAuth handshake. Offering Connect here was the false affordance:
    // `lifecycle` alone never means "needs OAuth".
    expect(
      canConnectRecord({
        lifecycle: "disconnected",
        credential: { display_name: "Fastmail IMAP" },
      } as unknown as Row),
    ).toBe(false);
  });

  test("treats an unselected credential as unknown, never as absent", () => {
    // A list that does not project `credential` reads `undefined`, which is not
    // evidence the row lacks one — so it offers no Connect either way.
    expect(canConnectRecord({ lifecycle: "connected" } as Row)).toBe(false);
    expect(canConnectRecord({ lifecycle: "disconnected" } as Row)).toBe(false);
  });
});

describe("integration inventory child routing", () => {
  const lookup = (resource: string, id: string) => `/${resource}/${id}`;

  test("opens only an explicitly available concrete child", () => {
    expect(concreteIntegrationHref({ concrete_target: {
      state: "AVAILABLE", resource: "agents.InferenceProvider", id: "provider-1",
    } } as unknown as Row, lookup)).toBe("/agents.InferenceProvider/provider-1");
  });

  test.each(["UNAVAILABLE", "AMBIGUOUS"])("keeps %s historical rows read-only", (state) => {
    expect(concreteIntegrationHref({ concrete_target: {
      state, resource: "agents.InferenceProvider", id: "hidden",
    } } as unknown as Row, lookup)).toBe("");
  });
});
