import { expectValidBaseAddon } from "@angee/app/testing";
import { describe, expect, test } from "vitest";

import agents from "./index";

describe("agents addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(agents)).not.toThrow();
  });

  test("registers the session route pair and global chatter contribution", () => {
    const sessionRoute = agents.routes?.find((route) => route.name === "agents.session");
    expect(sessionRoute?.parent).toBe("agents.sessions");
    expect(sessionRoute?.path).toBe("/agents/sessions/$id");
    expect(agents.chatter?.[0]?.id).toBe("agents");
  });
});
