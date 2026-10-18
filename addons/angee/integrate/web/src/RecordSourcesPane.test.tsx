// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import type { ChatterViewContext } from "@angee/ui";

const mocks = vi.hoisted(() => ({ query: vi.fn() }));

vi.mock("@angee/refine", async (original) => ({
  ...(await original<typeof import("@angee/refine")>()),
  useAuthoredQuery: mocks.query,
}));
vi.mock("@angee/ui", async (original) => ({
  ...(await original<typeof import("@angee/ui")>()),
  useRouteSearch: () => ({ source: "rlk_1" }),
  useNamespaceT: (_namespace: string, messages: Record<string, string>) =>
    (key: string, vars?: Record<string, unknown>) => (messages[key] ?? key).replace(
      /\{(\w+)\}/g,
      (_match, name: string) => String(vars?.[name] ?? `{${name}}`),
    ),
  NavLink: ({ children, href, ...props }: React.AnchorHTMLAttributes<HTMLAnchorElement>) =>
    <a href={href} {...props}>{children}</a>,
}));

import { RecordSourcesDocument } from "./documents";
import { RecordSourcesPane } from "./RecordSourcesPane";

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

test("renders every generic source fact and delegates the outbound URL", () => {
  render(<RecordSourcesPane context={context} />);

  expect(mocks.query).toHaveBeenCalledWith(RecordSourcesDocument, {
    modelLabel: "accounting.Bill",
    recordId: "bil_1",
  }, {
    enabled: true,
    models: ["integrate.RecordLink"],
    records: [{ model: "accounting.Bill", id: "bil_1" }],
  });
  expect(screen.getByText("Accounting source")).toBeTruthy();
  expect(screen.getByText("bills · company-1")).toBeTruthy();
  expect(screen.getByText("CURRENT")).toBeTruthy();
  expect(screen.getByText("Generation 7")).toBeTruthy();
  expect(screen.getByText("Last observed")).toBeTruthy();
  expect(screen.getByText("Applied base")).toBeTruthy();
  expect(screen.getByRole("link", { name: "account.move:42" }).getAttribute("href"))
    .toBe("https://source.test/42");
  expect(document.getElementById("source-rlk_1")?.className).toContain("border-border-strong");
});
