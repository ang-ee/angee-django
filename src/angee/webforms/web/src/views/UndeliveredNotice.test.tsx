// @vitest-environment happy-dom

import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
import { formatDate, type RecordToolbarContext } from "@angee/ui";

import { UndeliveredNotice } from "./UndeliveredNotice";

const capture = vi.hoisted(() => ({ resource: "", search: undefined as unknown }));

// The app's route owner resolves the responses list; here it answers with a fixed address
// and keeps the search it was asked to carry.
vi.mock("@angee/ui", async (importOriginal) => ({
  ...await importOriginal<typeof import("@angee/ui")>(),
  useResourceRoute: (resource: string, search?: unknown) => {
    capture.resource = resource;
    capture.search = search;
    return "/webforms/responses?filtered";
  },
}));

const EXPIRY = "2031-03-09T12:00:00+00:00";

function notice(record: RecordToolbarContext["record"], recordId: string | null = "wbf_7"): HTMLElement {
  return render(<UndeliveredNotice recordId={recordId} record={record} />).container;
}

afterEach(cleanup);

describe("UndeliveredNotice", () => {
  test("says how many responses wait and when the first is removed, as a warning", () => {
    const container = notice({ id: "wbf_7", undelivered_count: 3, undelivered_expires_at: EXPIRY });
    const alert = container.querySelector("[role=status]");
    expect(alert?.textContent).toContain(`3 undelivered, the first expires on ${formatDate(EXPIRY)}`);
    expect(alert?.textContent).toContain("A response is removed once the form's retention has passed, delivered or not.");
  });

  test("links to the responses list filtered to this form's pending and failed responses", () => {
    const container = notice({ id: "wbf_7", undelivered_count: 3, undelivered_expires_at: EXPIRY });
    const link = container.querySelector("a");
    expect(link?.textContent).toBe("Show undelivered responses");
    expect(link?.getAttribute("href")).toBe("/webforms/responses?filtered");
    expect(capture.resource).toBe("webforms.Response");
    const search = capture.search as { filter: string };
    expect(Object.keys(search)).toEqual(["filter"]);
    expect(JSON.parse(search.filter)).toEqual({
      delivery: { inList: ["pending", "failed"] },
      form: { exact: "wbf_7" },
    });
  });

  test("a form that keeps its responses says only how many wait", () => {
    const container = notice({ id: "wbf_7", undelivered_count: 1, undelivered_expires_at: null });
    expect(container.querySelector("[role=status]")?.textContent).toBe("1 undeliveredShow undelivered responses");
  });

  test("says nothing while none waits, and on a form not saved yet", () => {
    expect(notice({ id: "wbf_7", undelivered_count: 0, undelivered_expires_at: null }).childElementCount).toBe(0);
    expect(notice(null, null).childElementCount).toBe(0);
  });
});
