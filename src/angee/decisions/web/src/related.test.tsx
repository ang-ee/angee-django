// @vitest-environment happy-dom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { ShellPageTestProviders } from "@angee/app/testing";
import { createUiTestProviders } from "@angee/ui/testing";
import { createRouteHref, type ChatterViewContext } from "@angee/ui";
import type { CustomParams } from "@refinedev/core";

import { recordDecisionsGroup } from "./related";

const { Provider, clearClients } = createUiTestProviders({
  apiUrl: "test://decisions-related", queryClientConfig: { defaultOptions: { queries: { retry: false } } },
});
afterEach(() => { cleanup(); clearClients(); });

const context: ChatterViewContext = {
  pathname: "/notes/nte_7", params: { id: "nte_7" },
  route: { name: "notes.record", path: "/notes/$id", viewType: "record", modelLabel: "notes.Note", canonicalLabel: "notes.Note" },
  view: { kind: "record", type: "notes.Note", sqid: "nte_7" },
};

test("a record's Decisions group lists its open question first, then its answers, linking each decision", async () => {
  const custom = vi.fn(async (_params: Partial<CustomParams>) => ({ data: { decision_records: [
    { id: "dcr_1", decision: { id: "dcn_old", kind_label: "Which company?", is_open: false, verdict_label: "Use Apexive", created_at: "2026-10-01T10:00:00Z", answered_at: "2026-10-01T11:00:00Z", answered_by: { display_name: "Reviewer" } } },
    { id: "dcr_2", decision: { id: "dcn_open", kind_label: "Is this bill a duplicate?", is_open: true, verdict_label: "", created_at: "2026-10-03T10:00:00Z", answered_at: null, answered_by: null } },
  ] } }));
  function Group() {
    const label = (action?: React.ReactNode) => <li>Decisions{action}</li>;
    return <ul>{recordDecisionsGroup.content.render(context, label)}</ul>;
  }
  render(<Provider dataProvider={{ custom }}><ShellPageTestProviders runtime={{
    routeHref: createRouteHref([{ name: "decisions.inbox.record", path: "/decisions/$id" }]),
  }}><Group /></ShellPageTestProviders></Provider>);
  const rows = await screen.findAllByRole("link");
  expect(rows.map((row) => row.getAttribute("href"))).toEqual(["/decisions/dcn_open", "/decisions/dcn_old"]);
  expect(rows[0]!.textContent).toContain("Is this bill a duplicate?");
  expect(rows[0]!.textContent).toContain("Open");
  expect(rows[1]!.textContent).toContain("Use Apexive");
  expect(custom.mock.calls[0]?.[0].meta?.gqlVariables).toEqual({ model: "notes.Note", id: "nte_7" });
  expect(recordDecisionsGroup.content.when?.(context)).toBe(true);
  expect(recordDecisionsGroup.content.when?.({ ...context, route: { ...context.route!, modelLabel: "decisions.Decision", canonicalLabel: "decisions.Decision" } })).toBe(false);
});
