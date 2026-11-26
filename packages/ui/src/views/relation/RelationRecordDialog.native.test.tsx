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
import { FormView } from "../form/FormView";
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

/** A self-related, keyed resource: a node's parent is a node, and its create takes an idempotency key. */
const node = testDataResource("tree.Node", {
  recordRepresentation: "name",
  createArguments: [{ name: "idempotency_key", type: "String" }],
  fields: [id, text("name"), ...["parent", "sibling"].map((name): DataResourceFieldMetadata => ({
    name, kind: "relation", scalar: "ID", relationModelLabel: "tree.Node", relationObject: true, readable: true,
    aggregatable: false, creatable: true, updatable: true, requiredOnCreate: false,
  }))],
});

/** A tag-like resource whose create root takes an idempotency key, quick-created from its name. */
const label = testDataResource("labels.Label", {
  recordRepresentation: "name",
  createArguments: [{ name: "idempotency_key", type: "String" }],
  fields: [id, text("name")],
});

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

  fireEvent.click(screen.getByRole("combobox", { name: "Party" }));
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

test("a create form opened from a relation field names its draft under the form hosting the field: a chain of one resource's creates sends one key each", async () => {
  const { create, Wrapper } = harness([...resources, node]);
  render(<Wrapper><FormView resource="tree.Node" id={null} fields={[{ name: "name" }, { name: "parent", kind: "relation", widget: "many2one" }]} /></Wrapper>);

  // The page's create form, the parent's create form over it, and the grandparent's over that: all open at once.
  const createParent = async (within_: ReturnType<typeof within>, query: string) => {
    fireEvent.click(within_.getByRole("combobox", { name: "Parent" }));
    fireEvent.change(await screen.findByPlaceholderText("Search…"), { target: { value: query } });
    fireEvent.click(await screen.findByText(`Create “${query}”`));
  };
  await createParent(within(document.body), "Child");
  const [parent] = await screen.findAllByRole("dialog", { name: "New node" });
  await createParent(within(parent!), "Grand");
  // The parent's dialog is hidden from the accessibility tree, unnamed, while the grandparent's is over it.
  await waitFor(() => expect(screen.getAllByRole("dialog", { hidden: true })).toHaveLength(2));
  const grandparent = screen.getByRole("dialog", { name: "New node" });
  expect(within(grandparent).getByLabelText("Name")).toHaveProperty("value", "Grand");

  fireEvent.click(within(grandparent).getByRole("button", { name: "Create" }));
  await waitFor(() => expect(create).toHaveBeenCalledOnce());
  await waitFor(() => expect(screen.getAllByRole("dialog", { hidden: true })).toHaveLength(1));
  fireEvent.click(within(screen.getByRole("dialog", { name: "New node" })).getByRole("button", { name: "Create" }));
  await waitFor(() => expect(create).toHaveBeenCalledTimes(2));

  const keys = create.mock.calls.map(([call]) => (call as { meta?: { gqlVariables?: { idempotency_key?: string } } }).meta?.gqlVariables?.idempotency_key);
  expect(keys).toEqual([expect.any(String), expect.any(String)]);
  expect(keys[1]).not.toBe(keys[0]);
}, 20_000);

test("a relation field's create outside any form is a draft of its own beside a create form of the same resource", async () => {
  const { create, Wrapper } = harness([...resources, node]);
  const nodeRelation = { resource: "tree.Node", labelField: "name", canCreate: true };
  // A page's create form of a node, and a picker of nodes outside it (a toolbar's, a filter's): both open at once.
  render(<Wrapper>
    <FormView resource="tree.Node" id={null} fields={[{ name: "name" }]} />
    <RelationFieldWidget value={null} relation={nodeRelation} aria-label="Related node" />
  </Wrapper>);
  fireEvent.click(screen.getByRole("combobox", { name: "Related node" }));
  fireEvent.change(await screen.findByPlaceholderText("Search…"), { target: { value: "Leaf" } });
  fireEvent.click(await screen.findByText("Create “Leaf”"));
  const dialog = await screen.findByRole("dialog", { name: "New node" });
  fireEvent.click(within(dialog).getByRole("button", { name: "Create" }));
  await waitFor(() => expect(create).toHaveBeenCalledOnce());
  expect((create.mock.calls[0]?.[0] as { meta?: { gqlVariables?: { idempotency_key?: string } } }).meta?.gqlVariables?.idempotency_key)
    .toEqual(expect.any(String));
});

test("two relation fields to one resource keep their drafts apart: a lost create in one is retried under its own key after the other creates", async () => {
  const { create, Wrapper } = harness([...resources, node]);
  create.mockRejectedValueOnce(new TypeError("Failed to fetch"));
  render(<Wrapper><FormView resource="tree.Node" id={null}
    fields={[{ name: "name" }, { name: "parent", kind: "relation", widget: "many2one" }, { name: "sibling", kind: "relation", widget: "many2one" }]} /></Wrapper>);
  const createFrom = async (field: string, query: string) => {
    fireEvent.click(screen.getByRole("combobox", { name: field }));
    fireEvent.change(await screen.findByPlaceholderText("Search…"), { target: { value: query } });
    fireEvent.click(await screen.findByText(`Create “${query}”`));
    const dialog = await screen.findByRole("dialog", { name: "New node" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Create" }));
    return dialog;
  };
  // Field A's create loses its answer (it may have been written); the person closes the dialog.
  const lost = await createFrom("Parent", "Alpha");
  await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
  fireEvent.click(within(lost).getByRole("button", { name: "Close" }));
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "New node" })).toBeNull());
  // Field B creates something else, then field A's create is retried.
  await createFrom("Sibling", "Beta");
  await waitFor(() => expect(create).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "New node" })).toBeNull());
  await createFrom("Parent", "Alpha");
  await waitFor(() => expect(create).toHaveBeenCalledTimes(3));

  const keys = create.mock.calls.map(([call]) => (call as { meta?: { gqlVariables?: { idempotency_key?: string } } }).meta?.gqlVariables?.idempotency_key);
  expect(keys[1]).not.toBe(keys[0]);
  // A's retry carries A's key: the server replays the record it may have written, so it is written once.
  expect(keys[2]).toBe(keys[0]);
}, 20_000);

test("a quick create on a keyed root sends its draft's key through the create root the form uses: a retry after a lost answer carries it", async () => {
  const { create, Wrapper } = harness([...resources, label]);
  create.mockRejectedValueOnce(new TypeError("Failed to fetch"));
  const change = vi.fn();
  render(<Wrapper><RelationMultiFieldWidget value={[]} onChange={change} fieldName="labels"
    relation={{ resource: "labels.Label", labelField: "name", canCreate: true }} aria-label="Labels" /></Wrapper>);
  const quick = async (query: string, count: number) => {
    fireEvent.input(screen.getByRole("combobox", { name: "Labels" }), { target: { value: query }, inputType: "insertText" });
    fireEvent.click(await screen.findByRole("option", { name: `Create “${query}”` }));
    await waitFor(() => expect(create).toHaveBeenCalledTimes(count));
  };
  await quick("Urgent", 1);
  await quick("Urgent", 2);
  await waitFor(() => expect(change).toHaveBeenCalledOnce());
  await quick("Later", 3);
  const calls = create.mock.calls.map(([call]) => call as { resource?: string; meta?: { gqlVariables?: { idempotency_key?: string } } });
  // The same create the record form saves through: the root's resource, the key as its root argument.
  expect(calls.map((call) => call.resource)).toEqual(["labels", "labels", "labels"]);
  const keys = calls.map((call) => call.meta?.gqlVariables?.idempotency_key);
  expect(keys[0]).toEqual(expect.any(String));
  expect(keys[1]).toBe(keys[0]);
  expect(keys[2]).not.toBe(keys[0]);
});
