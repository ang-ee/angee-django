// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { Row } from "@angee/metadata";
import { afterEach, expect, test, vi } from "vitest";

import { testDataResource } from "@angee/metadata/testing";

import { ModalsHost, ToastProvider } from "../../feedback";
import { AppRuntimeProvider, containersFromChildren } from "../../runtime";
import { createUiTestProviders } from "../../testing";
import { defaultWidgets } from "../../widgets";
import { FORM_CONTAINERS, FormView } from "./FormView";
import { storedImplementationUnregistered } from "../resource/implementation-fields";
import { withModeLockedFields } from "./form-view-model";

const backend = {
  name: "backend", kind: "enum" as const, readable: true, aggregatable: false, creatable: true, updatable: true,
  createOnly: true, requiredOnCreate: false,
  values: [{ value: "LOCAL", description: "Local" }, { value: "STUB", description: "Stub" }],
};
const config = {
  name: "config", kind: "scalar" as const, scalar: "JSON", readable: true, aggregatable: false, creatable: true,
  updatable: true, requiredOnCreate: false,
};
const title = {
  name: "title", kind: "scalar" as const, scalar: "String", readable: true, aggregatable: false, creatable: true,
  updatable: true, requiredOnCreate: false,
};
const resource = testDataResource("notes.Note", { fields: [title, backend, config], implFields: ["backend"] });
const CARRIED = { root: "/srv/team", org: "local" };

function fixture(stored: Row) {
  let saved: Row = { ...stored };
  const getOne = vi.fn(async () => ({ data: saved }));
  const custom = vi.fn(async ({ meta }: { meta?: { gqlVariables?: Record<string, unknown> } }) => ({ data: {
    impl_choices: [
      { key: "local", category: "", defaults: { config: CARRIED }, config_schema: null },
      { key: "stub", category: "", defaults: { config: stored.config }, config_schema: null },
    ].map((choice) => ({ ...choice, asked: meta?.gqlVariables })),
  } }));
  const update = vi.fn(async ({ variables }: { variables: Row }) => {
    saved = { ...saved, ...variables, backend: String(variables.backend ?? saved.backend).toUpperCase() };
    return { data: saved };
  });
  const { Provider, clearClients } = createUiTestProviders({
    resources: [resource], apiUrl: "test://repair", router: true,
    queryClientConfig: { defaultOptions: { queries: { retry: false }, mutations: { retry: false } } },
  });
  render(<Provider dataProvider={{ getOne, custom, update }}><ModalsHost><ToastProvider>
    <AppRuntimeProvider runtime={{ widgets: defaultWidgets, containers: containersFromChildren(FORM_CONTAINERS, {}) }}>
      <FormView resource="notes.Note" id="note-1" fields={[
        { name: "title", title: true }, { name: "backend", label: "Backend" }, { name: "config", label: "Config" },
      ]} />
    </AppRuntimeProvider>
  </ToastProvider></ModalsHost></Provider>);
  return { custom, update, clearClients };
}

afterEach(cleanup);

test("a create-only selector holding a retired key is chosen once more and saves with the config it carries over", async () => {
  const stored = { id: "note-1", title: "First", backend: null, config: { root: "/srv/team", retired: true },
    permissions: ["read", "write"] };
  const f = fixture(stored);

  const control = await screen.findByRole("combobox", { name: "Backend" });
  expect(screen.getByText("Its implementation cannot run until another is chosen. Once chosen, it cannot be changed."))
    .toBeTruthy();
  // The read asked what choosing each implementation sets on this record.
  expect(f.custom.mock.calls.at(-1)?.[0]?.meta?.gqlVariables).toEqual({ model: "notes.Note", field: "backend", record: "note-1" });
  fireEvent.click(control);
  fireEvent.click(await screen.findByRole("option", { name: "Local" }));
  fireEvent.click(screen.getByRole("button", { name: "Save" }));

  // Nothing else changed: the request is the selector and the config the read said it carries over.
  await waitFor(() => expect(f.update).toHaveBeenCalledOnce());
  expect(f.update.mock.calls[0]?.[0]?.variables).toEqual({ backend: "LOCAL", config: CARRIED });
  // After the repair the stored key is registered: the selector is locked again.
  await waitFor(() => expect(screen.queryByRole("combobox", { name: "Backend" })).toBeNull());
  f.clearClients();
});

test("a create-only selector holding a registered key stays locked and its record is never asked about", async () => {
  const f = fixture({ id: "note-1", title: "First", backend: "LOCAL", config: CARRIED, permissions: ["read", "write"] });

  await waitFor(() => expect(screen.getAllByText("First").length).toBeGreaterThan(0));
  expect(screen.queryByRole("combobox", { name: "Backend" })).toBeNull();
  expect(f.custom.mock.calls.filter(([request]) => request?.meta?.gqlVariables?.record != null)).toEqual([]);
  f.clearClients();
});

test("the form offers the choice on the same three rows the server allows it on", () => {
  // The read contract joins the halves (the server's in test_integrate_graphql): a retired key reads null.
  const metadata = { resource, fields: { title, backend, config } } as unknown as Parameters<
    typeof storedImplementationUnregistered>[0];
  const offered = (stored: Row) => !withModeLockedFields([{ name: "backend", createOnly: true }], false,
    (field) => storedImplementationUnregistered(metadata, stored, field.name))[0]?.readOnly;
  expect([
    offered({ id: "note-1", backend: null }),
    offered({ id: "note-1", backend: "LOCAL" }),
    offered({ id: "note-1", backend: "STUB" }),
  ]).toEqual([true, false, false]);
});
