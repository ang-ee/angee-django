import { formatDate } from "@angee/ui";
import { describe, expect, test } from "vitest";

import { enWebformsMessages } from "./i18n";
import { isUndelivered, undeliveredFilter, undeliveredSummary } from "./undelivered";

/** The English messages with their variables filled, as the fragment's translator resolves them. */
const t = (key: string, vars: Record<string, unknown> = {}) =>
  (enWebformsMessages[key] ?? key).replace(/\{(\w+)\}/g, (_match, name: string) => String(vars[name]));

const EXPIRY = "2031-03-09T12:00:00+00:00";

describe("undelivered responses", () => {
  test("a response waits while it is pending or failed, however the server spells the value", () => {
    expect(["PENDING", "failed", "DELIVERED", "dismissed"].map((delivery) => isUndelivered({ delivery })))
      .toEqual([true, true, false, false]);
  });

  test("the filter selects pending and failed together, of every form or of one", () => {
    expect(undeliveredFilter()).toEqual({ delivery: { inList: ["pending", "failed"] } });
    expect(undeliveredFilter("wbf_7")).toEqual({
      delivery: { inList: ["pending", "failed"] },
      form: { exact: "wbf_7" },
    });
  });

  test("a form with none waiting says nothing", () => {
    expect(undeliveredSummary({ undelivered_count: 0, undelivered_expires_at: null }, t)).toBeNull();
    expect(undeliveredSummary({}, t)).toBeNull();
  });

  test("a form that keeps its responses says only how many wait", () => {
    expect(undeliveredSummary({ undelivered_count: 2, undelivered_expires_at: null }, t)).toBe("2 undelivered");
  });

  test("a form with retention also says when the first is removed, in the framework's date format", () => {
    expect(undeliveredSummary({ undelivered_count: 3, undelivered_expires_at: EXPIRY }, t))
      .toBe(`3 undelivered, the first expires on ${formatDate(EXPIRY)}`);
    expect(formatDate(EXPIRY)).toContain("2031");
  });
});
