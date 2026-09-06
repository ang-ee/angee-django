import { describe, expect, test } from "vitest";

import { errorMessage } from "./error-message";

describe("errorMessage", () => {
  test("returns Error.message and falls back for non-Error values", () => {
    expect(errorMessage(new Error("boom"), "fallback")).toBe("boom");
    expect(errorMessage("boom", "fallback")).toBe("fallback");
    expect(errorMessage({}, "fallback")).toBe("fallback");
    expect(errorMessage({ message: undefined }, "fallback")).toBe("fallback");
  });

  test("does not render transport request metadata", () => {
    const secret = "password-sentinel";
    const error = Object.assign(new Error(`GraphQL request variables: ${secret}`), {
      request: { variables: { password: secret } },
      response: { status: 500 },
    });
    expect(errorMessage(error, "fallback")).toBe("Request failed.");
  });

  test("keeps structured GraphQL domain messages", () => {
    const error = Object.assign(new Error("serialized request"), {
      graphQLErrors: [{ message: "Repository already exists.", extensions: { code: "VALIDATION" } }],
    });
    expect(errorMessage(error, "fallback")).toBe("Repository already exists.");
  });

  test("does not render an unclassified GraphQL exception message", () => {
    const error = Object.assign(new Error("serialized request"), {
      response: { errors: [{ message: "internal exception detail" }] },
      request: {},
    });
    expect(errorMessage(error, "fallback")).toBe("Request failed.");
  });
});
