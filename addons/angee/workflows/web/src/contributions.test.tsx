// @vitest-environment happy-dom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { ShellPageTestProviders } from "@angee/app/testing";
import { createUiTestProviders } from "@angee/ui/testing";
import { createRouteHref } from "@angee/ui";
import type { DocumentType } from "@angee/gql/console";
import type { CustomParams } from "@refinedev/core";

import { DecisionRunOrigin, decisionRunOrigin } from "./contributions";
import { DecisionWaitingRunsDocument } from "./documents.console";

vi.mock("@angee/decisions", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/decisions")>()),
  useDecision: () => ({ decision: { id: "dcn_review" } }),
}));

const { Provider, clearClients, clients } = createUiTestProviders({
  apiUrl: "test://workflow-origin", queryClientConfig: { defaultOptions: { queries: { retry: false } } },
});
afterEach(() => { cleanup(); clearClients(); });
type WaitingRuns = DocumentType<typeof DecisionWaitingRunsDocument>;
const visible: WaitingRuns = { steprun: [{ id: "wsr_review", node_label: "Review document", map_index: 0, is_mapped: false,
  run: { id: "wfr_review", display_name: "Record review" },
}] };

function origin(data: WaitingRuns = visible, error = false, pending = false) {
  const custom = vi.fn(async (_params: Partial<CustomParams>) => {
    if (pending) return new Promise<never>(() => {});
    if (error) throw new Error("Read unavailable.");
    return { data };
  });
  const result = render(<Provider dataProvider={{ custom }}><ShellPageTestProviders runtime={{
    routeHref: createRouteHref([{ name: "workflows.runs.record", path: "/workflows/runs/$id" }]),
  }}><DecisionRunOrigin /></ShellPageTestProviders></Provider>);
  return { ...result, custom };
}

test("decision origin asks for the current decision and links its waiting run and step", async () => {
  const { custom } = origin();
  expect((await screen.findByRole("link", { name: "Record review" })).getAttribute("href")).toBe("/workflows/runs/wfr_review");
  expect(screen.getByText(/· Review document$/)).toBeTruthy();
  expect(screen.queryByText(/\[0\]/)).toBeNull();
  expect(custom.mock.calls[0]?.[0].meta?.gqlVariables).toEqual({ decision: "dcn_review" });
  expect(custom.mock.calls[0]?.[0].meta?.gqlQuery).toBe(DecisionWaitingRunsDocument);
  const query = clients.flatMap((client) => client.getQueryCache().getAll())
    .find((entry) => entry.meta?.angeeRecords);
  expect(query?.meta?.angeeRecords).toEqual([{ model: "decisions.Decision", id: "dcn_review" }]);
  expect(query?.meta?.angeeRelatedModels).toEqual(["workflows.StepRun"]);
  expect(query?.meta?.angeeBroadModels).toBeUndefined();
});

test("mapped waiting rows show item zero without inferring mapping from the node key", async () => {
  origin({ steprun: visible.steprun.map((step) => ({ ...step, is_mapped: true })) });
  expect(await screen.findByText(/· Review document \[0\]/)).toBeTruthy();
});

test("the optional decision origin occupies no space while loading", () => {
  const { container } = origin(undefined, false, true);
  expect(container.textContent).toBe("");
  expect(screen.queryByRole("status")).toBeNull();
});

test.each([{ steprun: [] }, { steprun: [{ id: "wsr_hidden", node_label: "Hidden", map_index: 0, is_mapped: false, run: null }] }])(
  "decisions without a visible waiting run render no contribution: %j", async ({ steprun }) => {
    const { container, custom } = origin({ steprun });
    await vi.waitFor(() => expect(custom).toHaveBeenCalledOnce());
    await vi.waitFor(() => expect(container.textContent).toBe(""));
  },
);

test("failed origin reads show the shared error surface", async () => {
  origin(undefined, true);
  expect(await screen.findByText("Waiting runs are unavailable.")).toBeTruthy();
});

test("the decision origin child renders the waiting-run origin", () => {
  expect(decisionRunOrigin).toMatchObject({ content: { type: DecisionRunOrigin } });
});
