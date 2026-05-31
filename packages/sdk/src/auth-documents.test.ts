import { readFileSync } from "node:fs";

import { buildSchema, parse, validate } from "graphql";
import { describe, expect, test } from "vitest";

import {
  CURRENT_USER_DOCUMENT,
  LOGIN_DOCUMENT,
  LOGOUT_DOCUMENT,
} from "./auth-hooks";

const contract = buildSchema(
  readFileSync(new URL("../schema/contract.graphql", import.meta.url), "utf8"),
);

describe("auth documents", () => {
  for (const [name, document] of [
    ["currentUser", CURRENT_USER_DOCUMENT],
    ["login", LOGIN_DOCUMENT],
    ["logout", LOGOUT_DOCUMENT],
  ] as const) {
    test(`${name} validates against the contract`, () => {
      expect(validate(contract, parse(document)).map((e) => e.message)).toEqual([]);
    });
  }
});
