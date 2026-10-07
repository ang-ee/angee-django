// @vitest-environment happy-dom

import { cleanup, fireEvent, render, renderHook, screen, waitFor, within } from "@testing-library/react";
import { createMemoryHistory, createRootRoute, createRouter, RouterContextProvider } from "@tanstack/react-router";
import type { DataResourceFieldMetadata, DataResourceMetadata } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import type { ReactNode } from "react";
import { afterEach, expect, test, vi } from "vitest";

import { ModalsHost, ToastProvider } from "../../feedback";
import { AppRuntimeProvider } from "../../runtime";
import { createUiTestProviders } from "../../testing";
import { defaultWidgets } from "../../widgets";
import { RelationFieldWidget } from "./RelationFieldWidget";
import { RelationMultiFieldWidget } from "./RelationMultiFieldWidget";
import { useRelationForms } from "./RelationRecordDialog";

const id: DataResourceFieldMetadata = {
  name: "id", kind: "scalar", scalar: "ID", readable: true,
  aggregatable: false, creatable: false, updatable: false, requiredOnCreate: false,
};
const text = (name: string): DataResourceFieldMetadata => ({
  name, kind: "scalar", scalar: "String", readable: true,
  aggregatable: false, creatable: true, updatable: true, requiredOnCreate: false,
});
/** A concrete kind of the party parent: it shares the parent's ids and adds its own field. */
const kind = (label: string, list: string, field: string): DataResourceMetadata => testDataResource(label, {
  roots: { list }, canonicalLabel: "parties.Party", recordRepresentation: "display_name",
  fields: [id, text("display_name"), text(field)],
});
const party = testDataResource("parties.Party", {
  roots: { list: "parties", create: null, delete: null },
  capabilities: ["list", "detail", "update"],
  recordRepresentation: "display_name",
  concreteKinds: ["parties.Organization", "parties.Person"],
  fields: [id, text("display_name")],
});
const organization = kind("parties.Organization", "organizations", "legal_name");
const person = kind("parties.Person", "people", "given_name");
const tag = testDataResource("tags.Tag", { recordRepresentation: "name", fields: [id, text("name")] });
const resources = [party, organization, person, tag];

const partyRelation = { resource: "parties.Party", labelField: "display_name", canCreate: false };
const tagRelation = { resource: "tags.Tag", labelField: "name", canCreate: true };

const { Provider, clearClients } = createUiTestProviders({
  apiUrl: "test://relation-kinds",
  queryClientConfig: { defaultOptions: { queries: { retry: false }, mutations: { retry: false } } },
});
afterEach(() => { cleanup(); clearClients(); });

function harness(list: readonly DataResourceMetadata[] = resources) {
  const create = vi.fn(async ({ resource, variables }: { resource?: string; variables?: unknown }) => ({
    data: { id: `${resource}-new`, ...(variables as Record<string, unknown>) },
  }));
  const dataProvider = {
    create,
    getList: vi.fn(async () => ({ data: [], total: 0 })),
    getOne: vi.fn(async () => ({ data: { id: "people-new", display_name: "Ada" } })),
  };
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  function Wrapper({ children }: { children?: ReactNode }) {
    return <Provider resources={list} dataProvider={dataProvider}>
      <RouterContextProvider router={router}>
        <ModalsHost><ToastProvider>
          <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>{children}</AppRuntimeProvider>
        </ToastProvider></ModalsHost>
      </RouterContextProvider>
    </Provider>;
  }
  return { create, Wrapper };
}

test("an MTI parent creates through its creatable concrete kinds; a plain model creates itself", () => {
  const { Wrapper } = harness();
  const { result } = renderHook(() => ({
    party: useRelationForms(partyRelation, undefined).create,
    tag: useRelationForms(tagRelation, undefined).create,
  }), { wrapper: Wrapper });

  expect(result.current.party?.resource).toBe("parties.Party");
  expect(result.current.party?.kinds?.map(({ resource, label, prefillField }) => ({ resource, label, prefillField })))
    .toEqual([
      { resource: "parties.Organization", label: "Organization", prefillField: "display_name" },
      { resource: "parties.Person", label: "Person", prefillField: "display_name" },
    ]);
  expect(result.current.tag).toMatchObject({ resource: "tags.Tag", prefillField: "name" });
  expect(result.current.tag?.kinds).toBeUndefined();
});

test("a creatable parent leads its kinds, and a kind without a create root is not offered", () => {
  const creatableParty = { ...party, roots: { ...party.roots, create: "insert_parties_one" } };
  const listedOrganization = { ...organization, roots: { ...organization.roots, create: null } };
  const { Wrapper } = harness([creatableParty, listedOrganization, person]);
  const { result } = renderHook(
    () => useRelationForms({ ...partyRelation, canCreate: true }, undefined).create,
    { wrapper: Wrapper },
  );

  expect(result.current?.kinds?.map(({ resource, label }) => ({ resource, label }))).toEqual([
    { resource: "parties.Party", label: "Party" },
    { resource: "parties.Person", label: "Person" },
  ]);
});

test("the single picker's create switches kinds, saves the chosen kind and selects it as the parent", async () => {
  const { create, Wrapper } = harness();
  const change = vi.fn();
  render(<Wrapper><RelationFieldWidget value={null} onChange={change} relation={partyRelation} aria-label="Party" /></Wrapper>);

  fireEvent.click(screen.getByRole("button", { name: "Party" }));
  fireEvent.change(await screen.findByPlaceholderText("Search…"), { target: { value: "Ada" } });
  fireEvent.click(await screen.findByText("Create “Ada”"));

  const dialog = await screen.findByRole("dialog", { name: "New party" });
  const kinds = within(dialog).getByRole("group", { name: "Kind" });
  expect(within(kinds).getByRole("button", { name: "Organization" }).getAttribute("aria-pressed")).toBe("true");
  expect(await within(dialog).findByLabelText("Legal Name")).toBeTruthy();
  expect(within(dialog).queryByLabelText("Given Name")).toBeNull();

  fireEvent.click(within(kinds).getByRole("button", { name: "Person" }));
  expect(await within(dialog).findByLabelText("Given Name")).toBeTruthy();
  expect(within(dialog).queryByLabelText("Legal Name")).toBeNull();
  expect((within(dialog).getByLabelText("Display Name") as HTMLInputElement).value).toBe("Ada");

  fireEvent.change(within(dialog).getByLabelText("Given Name"), { target: { value: "Ada" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Create" }));

  await waitFor(() => expect(change).toHaveBeenCalledWith("people-new"));
  expect(create).toHaveBeenCalledOnce();
  expect(create.mock.calls[0]?.[0]).toMatchObject({
    resource: "people", variables: { display_name: "Ada", given_name: "Ada" },
  });
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "New party" })).toBeNull());
});

/** Type an unmatched query into a chips field and pick its "Create and edit…" option. */
async function createFromSearch(label: string, query: string): Promise<void> {
  fireEvent.input(screen.getByRole("combobox", { name: label }), { target: { value: query }, inputType: "insertText" });
  fireEvent.click(await screen.findByRole("option", { name: "Create and edit…" }));
}

test("the multi picker's create saves the default kind and adds it to the selection", async () => {
  const { create, Wrapper } = harness();
  const change = vi.fn();
  render(<Wrapper><RelationMultiFieldWidget value={["parties-1"]} onChange={change} relation={partyRelation} aria-label="Parties" /></Wrapper>);

  await createFromSearch("Parties", "Example Co");
  const dialog = await screen.findByRole("dialog", { name: "New party" });
  expect(within(dialog).getByRole("group", { name: "Kind" })).toBeTruthy();
  fireEvent.change(await within(dialog).findByLabelText("Display Name"), { target: { value: "Example Co" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Create" }));

  await waitFor(() => expect(change).toHaveBeenCalledWith(["parties-1", "organizations-new"]));
  expect(create.mock.calls[0]?.[0]).toMatchObject({ resource: "organizations", variables: { display_name: "Example Co" } });
});

test("a model without concrete kinds opens its own create form with no kind switcher", async () => {
  const { Wrapper } = harness();
  render(<Wrapper><RelationMultiFieldWidget value={[]} relation={tagRelation} aria-label="Tags" /></Wrapper>);

  await createFromSearch("Tags", "Follow up");
  const dialog = await screen.findByRole("dialog", { name: "New tag" });
  // The typed query names the new record.
  expect(await within(dialog).findByLabelText("Name")).toHaveProperty("value", "Follow up");
  expect(within(dialog).queryByRole("group", { name: "Kind" })).toBeNull();
});
