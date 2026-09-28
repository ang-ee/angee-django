// @vitest-environment happy-dom

import { expectValidBaseAddon } from "@angee/app/testing";
import { testDataResource, testQueryField, testResourceQuery } from "@angee/metadata/testing";
import type { RefineTestDataProvider } from "@angee/refine/testing";
import {
  AppRuntimeProvider,
  Field,
  FormView,
  ModalsHost,
  ToastProvider,
  baseIcons,
  defaultWidgets,
  formViewSectionsSlot,
} from "@angee/ui";
import { createUiTestProviders } from "@angee/ui/testing";
import { RouterContextProvider, createMemoryHistory, createRootRoute, createRouter } from "@tanstack/react-router";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import proposalsWork from "./index";

const scalarFields = ["id", "name"].map((name) => ({
  name, kind: "scalar" as const, scalar: "String", readable: true,
  aggregatable: false, creatable: true, updatable: true, requiredOnCreate: false,
}));
const resources = [
  testDataResource("proposals.Round", {
    recordRepresentation: "name",
    fields: [
      ...scalarFields,
      {
        name: "permissions", kind: "list", scalar: "String", readable: true,
        aggregatable: false, creatable: false, updatable: false, requiredOnCreate: false,
      },
      {
        name: "clarification_queue", kind: "relation", relationModelLabel: "work.Queue", relationObject: true,
        nullable: true, readable: true, aggregatable: false, creatable: true, updatable: true, requiredOnCreate: false,
      },
    ],
  }),
  testDataResource("work.Queue", {
    recordRepresentation: "name",
    fields: scalarFields,
    query: testResourceQuery({ fields: { name: testQueryField("name") } }),
  }),
];

const { Provider, clearClients } = createUiTestProviders({ apiUrl: "test://proposals-work" });
afterEach(() => { cleanup(); clearClients(); });

function renderRound(permissions: string[]) {
  const selected = { id: "queue-1", name: "Review queue" };
  const alternative = { id: "queue-2", name: "Delivery queue" };
  const record = { id: "round-1", name: "Review", permissions, clarification_queue: selected };
  const update = vi.fn(async () => ({ data: record }));
  const provider = {
    getOne: vi.fn(async () => ({ data: record })),
    getList: vi.fn(async () => ({ data: [selected, alternative], total: 2 })),
    update,
  } satisfies RefineTestDataProvider;
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  render(
    <Provider resources={resources} dataProvider={provider}
      queryClientConfig={{ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } }}>
      <RouterContextProvider router={router}>
        <ModalsHost><ToastProvider><AppRuntimeProvider runtime={{
          widgets: defaultWidgets, icons: baseIcons, slots: proposalsWork.slots,
        }}>
          <FormView resource="proposals.Round" id="round-1">
            <Field name="name" label="Name" title />
          </FormView>
        </AppRuntimeProvider></ToastProvider></ModalsHost>
      </RouterContextProvider>
    </Provider>,
  );
  return { update, provider };
}

test("contributes one queue section to the round form", () => {
  expectValidBaseAddon(proposalsWork);
  expect(proposalsWork.slots).toHaveLength(1);
  expect(proposalsWork.slots?.[0]).toMatchObject({
    ...formViewSectionsSlot("proposals.Round"), id: "proposals-work.questions",
  });
});

test("renders the standard relation picker and saves through the round form", async () => {
  const { update } = renderRound(["manage", "write"]);
  const picker = await screen.findByRole("button", { name: /Questions queue/ });
  expect(picker.hasAttribute("disabled")).toBe(false);
  fireEvent.click(picker);
  fireEvent.click(await screen.findByRole("option", { name: "Delivery queue" }));
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(update).toHaveBeenCalledOnce());
  expect(update.mock.calls[0]).toEqual([expect.objectContaining({
    variables: expect.objectContaining({ clarification_queue: "queue-2" }),
  })]);
});

test("keeps the queue visible and read-only without round write", async () => {
  const { update, provider } = renderRound(["respond", "ask"]);
  await screen.findByText("Review queue");
  const picker = screen.queryByRole("button", { name: /Questions queue/ });
  expect(picker === null || picker.hasAttribute("disabled")).toBe(true);
  expect(provider.getList).not.toHaveBeenCalled();
  expect(update).not.toHaveBeenCalled();
});
