// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { GetListParams } from "@refinedev/core";
import { OperationDocumentsProvider } from "@angee/refine";
import { operationDocuments } from "@angee/gql/console/actions";
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from "@tanstack/react-router";
import { AppRuntimeProvider, baseIcons, defaultWidgets, ModalsHost, ToastProvider } from "@angee/ui";
import { createRouteHref, routeSearchString } from "@angee/ui/runtime";
import { createUiTestProviders } from "@angee/ui/testing";
import { afterEach, beforeAll, expect, test, vi } from "vitest";

import { RunsPage } from "./RunsPage";
import { workflowVersionFixture } from "./catalogue/testing";
import { runFixture, runResourceFixture, runSubjectFixture, workflowResourceFixture } from "./testing";

const { Provider, clearClients } = createUiTestProviders({
  apiUrl: "test://workflow-runs",
  queryClientConfig: { defaultOptions: { queries: { retry: false, staleTime: Infinity } } },
});
beforeAll(() => { Element.prototype.getAnimations ??= () => []; });
afterEach(() => { cleanup(); clearClients(); });

function fixture(initialEntry = "/") {
  const run = runFixture();
  const getList = vi.fn(async (params: GetListParams) => ({
    data: params.resource === "workflow" || params.resource === "workflows.Workflow"
      ? [{ id: "wfl_review", key: "record_review", name: "Record review", subject_model: "notes.Note" }] : [run], total: 1,
  }));
  const root = createRootRoute({
    validateSearch: (search: Record<string, unknown>) => search,
    component: () => <AppRuntimeProvider runtime={{ widgets: defaultWidgets, icons: baseIcons,
      routeHref: createRouteHref([{ name: "workflows.runs.record", path: "/workflows/runs/$id" }]),
    }}><ModalsHost><ToastProvider><RunsPage /></ToastProvider></ModalsHost></AppRuntimeProvider>,
  });
  const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: [initialEntry] }),
    parseSearch: (value) => Object.fromEntries(new URLSearchParams(value)),
    stringifySearch: (value) => { const query = routeSearchString(value); return query ? `?${query}` : ""; },
  });
  const getOne = vi.fn(async ({ resource, id }: { resource: string; id?: string | number }) => ({ data:
    resource === "workflow" || resource === "workflows.Workflow" ? { id, name: "Record review" } : { id, display_name: "Review notes" },
  }));
  const custom = vi.fn(async () => ({ data: { workflowrun_groups: [
    { key: { status: "FAILED", workflow_id: "wfl_review", workflow__name: "Record review" }, aggregate: { count: 1 } },
    { key: { status: "WAITING", workflow_id: "wfl_other", workflow__name: "Another workflow" }, aggregate: { count: 1 } },
  ], totalCount: 2 } }));
  render(<Provider resources={[runResourceFixture, runSubjectFixture, workflowResourceFixture, workflowVersionFixture]} dataProvider={{ getList, getOne, custom }}>
    <OperationDocumentsProvider documents={{ console: operationDocuments }}><RouterProvider router={router} /></OperationDocumentsProvider>
  </Provider>);
  return { router, runCalls: () => getList.mock.calls.filter(([params]) => params.resource === "workflowrun" || params.resource === "workflows.WorkflowRun") };
}

test("reads runs through the resource owner and resolves subject display names", async () => {
  const { runCalls } = fixture();
  expect(await screen.findByText("Review notes")).toBeTruthy();
  expect(screen.getByText("Manual")).toBeTruthy();
  expect(screen.getAllByRole("link").some((link) => link.getAttribute("href") === "/workflows/runs/wfr_review")).toBe(true);
  expect(runCalls()[0]?.[0].meta?.gqlVariables?.order_by).toEqual({ created_at: "desc" });
  expect(runCalls()[0]?.[0].meta?.fields).toEqual(expect.arrayContaining([
    "status", "origin", { version: [{ workflow: ["name"] }] }, "subject_id", "created_at", "finished_at", "outcome",
  ]));
  expect(screen.queryByRole("button", { name: /New/ })).toBeNull();
});

test("restores exact status and workflow filters from the route", async () => {
  const { runCalls } = fixture(`/?${new URLSearchParams({ filter: JSON.stringify({ status: { exact: "failed" }, workflow: { exact: "wfl_review" } }) })}`);
  await screen.findByText("Review notes");
  expect(runCalls()[0]?.[0].meta?.gqlVariables?.where).toEqual({
    _and: [{ status: { _eq: "failed" } }, { workflow: { _eq: "wfl_review" } }],
  });
});

test("changes status through the native control while preserving other route filters", async () => {
  const { router, runCalls } = fixture(`/?${new URLSearchParams({ filter: JSON.stringify({ workflow: { exact: "wfl_review" } }), keep: "external", page: "3" })}`);
  await screen.findByText("Review notes");
  expect(screen.queryByRole("combobox", { name: "Run status" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: /^Filter.*group/i }));
  fireEvent.click(await screen.findByRole("button", { name: "Waiting" }));
  await waitFor(() => expect(router.state.location.search).toMatchObject({ keep: "external" }));
  expect(router.state.location.search).not.toHaveProperty("runStatus");
  expect(router.state.location.search).not.toHaveProperty("page");
  await waitFor(() => expect(runCalls().at(-1)?.[0].meta?.gqlVariables?.where).toEqual({
    _and: [{ status: { _eq: "waiting" } }, { workflow: { _eq: "wfl_review" } }],
  }));
});

test("clears a workflow filter without introducing a record editing path", async () => {
  const { router, runCalls } = fixture(`/?${new URLSearchParams({ filter: JSON.stringify({ workflow: { exact: "wfl_review" }, status: { exact: "failed" } }), keep: "external" })}`);
  await screen.findByText("Review notes");
  fireEvent.click(screen.getByRole("button", { name: /^Filter.*group/i }));
  fireEvent.click(await screen.findByRole("button", { name: "Record review" }));
  await waitFor(() => expect(router.state.location.search).toMatchObject({ keep: "external" }));
  expect(router.state.location.search).not.toHaveProperty("workflow");
  await waitFor(() => expect(runCalls().at(-1)?.[0].meta?.gqlVariables?.where).toEqual({ status: { _eq: "failed" } }));
  expect(screen.queryByRole("button", { name: /Create|Edit|New/ })).toBeNull();
});
