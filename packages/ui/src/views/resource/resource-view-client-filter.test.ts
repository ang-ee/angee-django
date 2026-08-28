import { afterEach, describe, expect, test, vi } from "vitest";
import {
  ANGEE_FILTER_CODEC_LOOKUP_OPERATORS,
  crudFiltersFromFilterRecord,
} from "@angee/refine";

import { matchesLocalLookup } from "./resource-view-client-filter";

type LookupCase = {
  operator: (typeof ANGEE_FILTER_CODEC_LOOKUP_OPERATORS)[number];
  value: unknown;
  operand: unknown;
  mismatch?: unknown;
  locallyEvaluated?: boolean;
};

const LOOKUP_CASES: readonly LookupCase[] = [
  { operator: "exact", value: "alpha", operand: "alpha", mismatch: "beta" },
  { operator: "sqid", value: { id: "rel_1" }, operand: "rel_1", mismatch: "rel_2" },
  { operator: "pk", value: { id: "rel_1" }, operand: "rel_1", mismatch: "rel_2" },
  { operator: "_eq", value: "alpha", operand: "alpha", mismatch: "beta" },
  { operator: "ne", value: "alpha", operand: "beta", mismatch: "alpha" },
  { operator: "_neq", value: "alpha", operand: "beta", mismatch: "alpha" },
  { operator: "gt", value: 5, operand: 4, mismatch: 5 },
  { operator: "_gt", value: 5, operand: 4, mismatch: 5 },
  { operator: "gte", value: 5, operand: 5, mismatch: 6 },
  { operator: "_gte", value: 5, operand: 5, mismatch: 6 },
  { operator: "lt", value: 5, operand: 6, mismatch: 5 },
  { operator: "_lt", value: 5, operand: 6, mismatch: 5 },
  { operator: "lte", value: 5, operand: 5, mismatch: 4 },
  { operator: "_lte", value: 5, operand: 5, mismatch: 4 },
  { operator: "inList", value: "alpha", operand: ["alpha"], mismatch: ["beta"] },
  { operator: "_in", value: "alpha", operand: ["alpha"], mismatch: ["beta"] },
  { operator: "_nin", value: "alpha", operand: ["beta"], mismatch: ["alpha"] },
  { operator: "isNull", value: null, operand: true, mismatch: false },
  { operator: "_is_null", value: null, operand: true, mismatch: false },
  { operator: "jsonContains", value: { kind: "note" }, operand: { kind: "note" }, locallyEvaluated: false },
  { operator: "_contains", value: { kind: "note" }, operand: { kind: "note" }, locallyEvaluated: false },
  { operator: "contains", value: "Alphabet", operand: "Alpha", mismatch: "alpha" },
  { operator: "iContains", value: "Alphabet", operand: "alpha", mismatch: "omega" },
  { operator: "startsWith", value: "Alphabet", operand: "Alpha", mismatch: "alpha" },
  { operator: "iStartsWith", value: "Alphabet", operand: "alpha", mismatch: "omega" },
  { operator: "endsWith", value: "Alphabet", operand: "bet", mismatch: "BET" },
  { operator: "iEndsWith", value: "Alphabet", operand: "BET", mismatch: "omega" },
];

afterEach(() => {
  vi.restoreAllMocks();
});

describe("client filter vocabulary", () => {
  test("covers every transport-codec lookup spelling", () => {
    expect(LOOKUP_CASES.map(({ operator }) => operator)).toEqual(
      ANGEE_FILTER_CODEC_LOOKUP_OPERATORS,
    );
  });

  test.each(LOOKUP_CASES)("the codec accepts $operator", ({ operator, operand }) => {
    expect(crudFiltersFromFilterRecord({ field: { [operator]: operand } }))
      .not.toEqual([]);
  });

  test.each(LOOKUP_CASES.filter(({ locallyEvaluated }) => locallyEvaluated !== false))(
    "the local evaluator implements $operator",
    ({ operator, value, operand, mismatch }) => {
      expect(matchesLocalLookup(value, { [operator]: operand })).toBe(true);
      expect(matchesLocalLookup(value, { [operator]: mismatch })).toBe(false);
    },
  );

  test("ne rejects equality instead of silently matching every row", () => {
    expect(matchesLocalLookup("active", { ne: "active" })).toBe(false);
    expect(matchesLocalLookup("active", { ne: "draft" })).toBe(true);
  });

  test.each(["jsonContains", "_contains"])(
    "warns and ignores locally unevaluable %s lookups",
    (operator) => {
      const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
      expect(matchesLocalLookup({ kind: "note" }, {
        [operator]: { kind: "other" },
      })).toBe(true);
      expect(warn).toHaveBeenCalledWith(expect.stringContaining(operator));
    },
  );

  test.each(["iExact", "futureLookup"])(
    "warns and ignores unsupported %s lookups",
    (operator) => {
      const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
      expect(matchesLocalLookup("Alpha", { [operator]: "Beta" })).toBe(true);
      expect(warn).toHaveBeenCalledWith(expect.stringContaining(operator));
    },
  );

  test("recurses through codec-legal nested field paths", () => {
    const value = { product: { name: "Alpha notebook" } };
    expect(matchesLocalLookup(value, {
      product: { name: { iContains: "NOTE" } },
    })).toBe(true);
    expect(matchesLocalLookup(value, {
      product: { name: { iContains: "phone" } },
    })).toBe(false);
  });

  test.each([
    ["inList", "alpha"],
    ["_nin", "alpha"],
    ["isNull", "yes"],
    ["iContains", 42],
    ["startsWith", null],
  ])("warns and ignores malformed %s operands", (operator, operand) => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    expect(matchesLocalLookup("alpha", { [operator]: operand })).toBe(true);
    expect(warn).toHaveBeenCalledWith(expect.stringContaining(operator));
  });

  test("warns only once per ignored operator", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    expect(matchesLocalLookup("alpha", { repeatedUnknown: 1 })).toBe(true);
    expect(matchesLocalLookup("alpha", { repeatedUnknown: 2 })).toBe(true);
    expect(warn).toHaveBeenCalledTimes(1);
  });
});
