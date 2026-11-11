import { expectValidBaseAddon } from "@angee/app/testing";
import { describe, expect, test } from "vitest";

import timeline from "./index";

describe("timeline fragment", () => {
  test("satisfies the shared manifest contracts", () => expect(() => expectValidBaseAddon(timeline)).not.toThrow());
});
