import { describe, expect, test } from "vitest";
import type { Row } from "@angee/metadata";

import { concreteIntegrationHref } from "./IntegrationsPage";

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
