// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import type { ChatterViewContext } from "@angee/ui";

const mocks = vi.hoisted(() => ({ query: vi.fn() }));

vi.mock("@angee/refine", async (original) => ({
  ...(await original<typeof import("@angee/refine")>()),
  useAuthoredQuery: mocks.query,
}));

import { RecordSourcesDocument } from "./documents";
import { RecordSourcesPane, recordSourcesGroup } from "./RecordSourcesPane";

const context: ChatterViewContext = {
  pathname: "/bills/bil_1",
  params: { id: "bil_1" },
  route: {
    name: "bills.record",
    path: "/bills/$id",
    viewType: "accounting/bill",
    modelLabel: "accounting.Bill",
    recordEdges: ["integrate.RecordLink"],
  },
  view: { kind: "record", type: "accounting/bill", sqid: "bil_1" },
};

beforeEach(() => {
  mocks.query.mockReset().mockReturnValue({
    data: { record_links: [{
      id: "rlk_1",
      external_key: "account.move:42",
      status: "CURRENT",
      source_url: "https://source.test/42",
      last_verified_generation: 7,
      last_seen_at: "2026-10-18T14:30:00Z",
      applied_at: "2026-10-18T14:31:00Z",
      stream: {
        id: "stm_1",
        key: "bills",
        partition: "company-1",
        integration: { id: "int_1", display_name: "Accounting source" },
      },
    }] },
    error: null,
    isLoading: false,
  });
});
afterEach(cleanup);

test("the Sources group counts and lists each source as a row linking to its outbound URL", () => {
  expect(recordSourcesGroup.content.when?.(context)).toBe(true);
  function Count() { return <span>Count {recordSourcesGroup.content.useCount?.(context)}</span>; }
  render(<><Count /><RecordSourcesPane context={context} /></>);

  expect(mocks.query).toHaveBeenCalledWith(RecordSourcesDocument, {
    modelLabel: "accounting.Bill",
    recordId: "bil_1",
  }, {
    enabled: true,
    models: ["integrate.RecordLink"],
    records: [{ model: "accounting.Bill", id: "bil_1" }],
  });
  expect(screen.getByText("Count 1")).toBeTruthy();
  const row = screen.getByRole("link", { name: /Accounting source/ });
  expect(row.getAttribute("href")).toBe("https://source.test/42");
  expect(row.textContent).toContain("bills · company-1");
  expect(row.textContent).toContain("CURRENT");
  expect(row.textContent).toContain("account.move:42");
});
