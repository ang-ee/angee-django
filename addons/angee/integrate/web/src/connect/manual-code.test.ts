import { describe, expect, test } from "vitest";

import { parseManualCode } from "./ConnectOAuthButton";

const t = (key: string) => key;

describe("parseManualCode", () => {
  test("splits code and state on the final hash", () => {
    expect(parseManualCode("opaque#code#state-token", "state-token", t)).toEqual({
      code: "opaque#code",
      state: "state-token",
    });
  });

  test("allows callers without an expected state", () => {
    expect(parseManualCode("code#returned-state", "", t)).toEqual({
      code: "code",
      state: "returned-state",
    });
  });

  test("rejects incomplete or mismatched manual codes", () => {
    expect(() => parseManualCode("code-only", "", t)).toThrow(
      "providers.connect.codeIncomplete",
    );
    expect(() => parseManualCode("code#other-state", "state", t)).toThrow(
      "providers.connect.codeMismatch",
    );
  });
});
