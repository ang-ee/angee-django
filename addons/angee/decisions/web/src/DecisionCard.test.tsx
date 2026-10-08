// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { testDataResource, testQueryField, testResourceQuery } from "@angee/metadata/testing";
import { AppRuntimeProvider, createRouteHref, defaultWidgets, ModalsHost, ToastProvider } from "@angee/ui";
import { afterEach, expect, test, vi } from "vitest";
import { createUiTestProviders } from "@angee/ui/testing";
import { DecisionCard } from "./DecisionCard";
import { decisionFixture } from "./testing";
import { useState } from "react";
import { RouterContextProvider, createMemoryHistory, createRootRoute, createRouter } from "@tanstack/react-router";

const decide = vi.hoisted(() => vi.fn());
vi.mock("@angee/ui", async (original) => ({ ...await original<typeof import("@angee/ui")>(), useAuthoredResourceMutation: () => [decide] }));
const { Provider, clearClients } = createUiTestProviders();
afterEach(() => { cleanup(); clearClients(); vi.clearAllMocks(); });

test("choices reset with the revision and an accepted answer remains locked until it closes", async () => {
  decide.mockResolvedValue({ decide: { ok: true, message: "" } });
  const decision = decisionFixture();
  const view = render(<Provider><DecisionCard decision={decision} /></Provider>);
  fireEvent.click(screen.getByRole("radio", { name: /^Accept/ }));
  view.rerender(<Provider><DecisionCard decision={{ ...decision, revision: 4 }} /></Provider>);
  expect((screen.getByRole("button", { name: "Confirm" }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getByRole("radio", { name: /^Accept/ }).getAttribute("aria-checked")).toBe("false");
  fireEvent.click(screen.getByRole("radio", { name: /^Accept/ }));
  fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
  await waitFor(() => expect(decide).toHaveBeenCalledExactlyOnceWith({ id: decision.id, revision: 4, chosen: ["accept"], values: {} }));
  await waitFor(() => expect((screen.getByRole("button", { name: "Confirm" }) as HTMLButtonElement).disabled).toBe(true));
  fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
  expect(decide).toHaveBeenCalledTimes(1);
  view.rerender(<Provider><DecisionCard decision={{ ...decision, revision: 4, is_open: false, verdict: ["accept"], verdict_label: "Accept" }} /></Provider>);
  expect(document.activeElement?.textContent).toContain("Chose: Accept");
});

const contact = testDataResource("contacts.Contact", {
  recordRepresentation: "name", roots: { aggregate: "contacts_aggregate" },
  typeNames: { filter: "contacts_bool_exp", order: "contacts_order_by" },
  fields: [{ name: "name", label: "Name", kind: "scalar", scalar: "String", readable: true,
    creatable: true, updatable: true, requiredOnCreate: true, aggregatable: false }],
  query: testResourceQuery({ fields: { id: testQueryField("id"), name: testQueryField("name", {
    filter: { field: "name", scalar: "String", values: [], operators: ["exact", "iContains"] },
  }) } }),
});
const record = testDataResource("notes.Note", { fields: [
  { name: "contact", label: "Contact", kind: "relation", scalar: null, relationModelLabel: "contacts.Contact",
    readable: true, creatable: true, updatable: true, requiredOnCreate: false, aggregatable: false },
  { name: "title", label: "Title", kind: "scalar", scalar: "String", readable: true,
    creatable: true, updatable: true, requiredOnCreate: false, aggregatable: false },
  { name: "enabled", label: "Enabled", kind: "scalar", scalar: "Boolean", readable: true,
    creatable: true, updatable: true, requiredOnCreate: false, aggregatable: false },
  { name: "contacts", label: "Contacts", kind: "list", scalar: null, relationModelLabel: "contacts.Contact",
    readable: true, creatable: true, updatable: true, requiredOnCreate: false, aggregatable: false },
] });
const where = { name: { _neq: "Hidden" } };
const chooseDecision = (multiple = false) => decisionFixture({ proposal: { multiple, alternatives: [
  { key: "other", label: "Another contact", outcome: "matched", actions: { nte_7: { fields: { contact: { choose: { filter: where } } } } } },
  { key: "rename", label: "Rename", outcome: "done", actions: { nte_7: { fields: { title: { choose: {} }, enabled: { choose: {} } } } } },
] } });
function ChooseHarness({ initial = chooseDecision(), dataProvider = {} }: { initial?: ReturnType<typeof decisionFixture>; dataProvider?: Parameters<typeof Provider>[0]["dataProvider"] }) {
  const [decision, setDecision] = useState(initial);
  const [router] = useState(() => createRouter({ routeTree: createRootRoute(), history: createMemoryHistory() }));
  return <RouterContextProvider router={router}><Provider resources={[record, contact]} dataProvider={dataProvider}>
    <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}><ModalsHost><ToastProvider>
      <button onClick={() => setDecision({ ...decision, revision: decision.revision + 1 })}>Revise</button>
      <DecisionCard decision={decision} selfId="nte_7" />
    </ToastProvider></ModalsHost></AppRuntimeProvider>
  </Provider></RouterContextProvider>;
}
const confirmDisabled = () => (screen.getByRole("button", { name: "Confirm" }) as HTMLButtonElement).disabled;

test("a relation choose uses server search and filters, gates Confirm, sends public ids, and resets values on revision", async () => {
  decide.mockResolvedValue({ decide: { ok: true, message: "" } });
  const selected = { id: "cnt_1", name: "Visible contact" };
  const getList = vi.fn(async (_params: unknown) => ({ data: [selected], total: 1 }));
  const getOne = vi.fn(async () => ({ data: selected }));
  render(<ChooseHarness dataProvider={{ getList, getOne }} />);
  expect(screen.queryByRole("button", { name: "Contact" })).toBeNull();
  expect(confirmDisabled()).toBe(true);
  fireEvent.click(screen.getByRole("radio", { name: /^Another contact/ }));
  expect(confirmDisabled()).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Contact" }));
  fireEvent.change(await screen.findByPlaceholderText("Search…"), { target: { value: "Visible" } });
  await waitFor(() => expect(getList.mock.calls.at(-1)?.[0]).toMatchObject({
    filters: [{ operator: "or", value: [{ field: "name", operator: "contains", value: "Visible" }] }],
    meta: { gqlVariables: { where } },
  }));
  fireEvent.click(await screen.findByRole("option", { name: selected.name }));
  expect(confirmDisabled()).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "Revise" }));
  fireEvent.click(screen.getByRole("radio", { name: /^Another contact/ }));
  expect(confirmDisabled()).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Contact" }));
  fireEvent.click(await screen.findByRole("option", { name: selected.name }));
  fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
  await waitFor(() => expect(decide).toHaveBeenCalledExactlyOnceWith({ id: "dcn_review",
    revision: 4, chosen: ["other"], values: { nte_7: { contact: selected.id } },
  }));
});

test.each([false, true])("a many-to-many choose renders the multi widget and submits a list (clear: %s)", async (clear) => {
  decide.mockResolvedValue({ decide: { ok: true, message: "" } });
  const contacts = [{ id: "cnt_1", name: "First contact" }, { id: "cnt_2", name: "Second contact" }];
  const getList = vi.fn(async (_params: unknown) => ({ data: contacts, total: contacts.length }));
  const initial = decisionFixture({ proposal: { alternatives: [{ key: "select", label: "Select contacts", outcome: "done",
    actions: { nte_7: { fields: { contacts: { choose: { filter: where } } } } },
  }] } });
  render(<ChooseHarness initial={initial} dataProvider={{ getList }} />);
  fireEvent.click(screen.getByRole("radio", { name: /^Select contacts/ }));
  expect(confirmDisabled()).toBe(true);
  await waitFor(() => expect(getList.mock.calls.at(-1)?.[0]).toMatchObject({ meta: { gqlVariables: { where } } }));
  const search = await screen.findByRole("combobox", { name: "Contacts" });
  for (const contact of contacts) {
    // The chips field searches as the reader types and adds the picked option as a chip.
    fireEvent.input(search, { target: { value: contact.name }, inputType: "insertText" });
    fireEvent.click(await screen.findByRole("option", { name: contact.name }));
  }
  if (clear) for (const contact of contacts) fireEvent.click(screen.getByRole("button", { name: `Remove ${contact.name}` }));
  expect(confirmDisabled()).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
  await waitFor(() => expect(decide).toHaveBeenCalledExactlyOnceWith({ id: initial.id, revision: initial.revision,
    chosen: ["select"], values: { nte_7: { contacts: clear ? [] : contacts.map(({ id }) => id) } },
  }));
});

test("inline create saves immediately and selects the returned record for the answer", async () => {
  decide.mockResolvedValue({ decide: { ok: true, message: "" } });
  const created = { id: "cnt_new", name: "New contact" };
  const getList = vi.fn(async (_params: unknown) => ({ data: [], total: 0 }));
  const create = vi.fn(async (_params: unknown) => ({ data: created }));
  render(<ChooseHarness dataProvider={{ getList, create, getOne: async () => ({ data: created }) }} />);
  fireEvent.click(screen.getByRole("radio", { name: /^Another contact/ }));
  fireEvent.click(screen.getByRole("button", { name: "Contact" }));
  fireEvent.change(await screen.findByPlaceholderText("Search…"), { target: { value: created.name } });
  fireEvent.click(await screen.findByText(`Create “${created.name}”`));
  const dialog = await screen.findByRole("dialog");
  expect((within(dialog).getByRole("textbox", { name: "Name" }) as HTMLInputElement).value).toBe(created.name);
  fireEvent.click(within(dialog).getByRole("button", { name: "Create" }));
  await waitFor(() => expect(create).toHaveBeenCalledOnce());
  expect(create.mock.calls[0]?.[0]).toMatchObject({ variables: { name: created.name } });
  await screen.findByRole("button", { name: `Contact: ${created.name}` });
  expect(confirmDisabled()).toBe(false);
  expect(decide).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
  await waitFor(() => expect(decide).toHaveBeenCalledWith({ id: "dcn_review",
    revision: 3, chosen: ["other"], values: { nte_7: { contact: created.id } },
  }));
});

test("every chosen field gates Confirm and unchosen values are omitted", async () => {
  decide.mockResolvedValue({ decide: { ok: true, message: "" } });
  const selected = { id: "cnt_1", name: "Visible contact" };
  render(<ChooseHarness initial={chooseDecision(true)} dataProvider={{ getList: async () => ({ data: [selected], total: 1 }),
    getOne: async () => ({ data: selected }) }} />);
  fireEvent.click(screen.getByRole("checkbox", { name: "Rename" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Title" }), { target: { value: "New title" } });
  expect(confirmDisabled()).toBe(true);
  fireEvent.click(screen.getByRole("switch", { name: "Enabled" }));
  fireEvent.click(screen.getByRole("switch", { name: "Enabled" }));
  expect(confirmDisabled()).toBe(false);
  fireEvent.click(screen.getByRole("checkbox", { name: "Another contact" }));
  expect(confirmDisabled()).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Contact" }));
  fireEvent.click(await screen.findByRole("option", { name: selected.name }));
  expect(confirmDisabled()).toBe(false);
  fireEvent.click(screen.getByRole("checkbox", { name: "Another contact" }));
  expect(confirmDisabled()).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
  await waitFor(() => expect(decide).toHaveBeenCalledWith({ id: "dcn_review",
    revision: 3, chosen: ["rename"], values: { nte_7: { title: "New title", enabled: false } },
  }));
});

test("a closed card resolves the persisted chosen relation's value label", async () => {
  const selected = { id: "cnt_1", name: "Visible contact" };
  render(<ChooseHarness initial={{ ...chooseDecision(), is_open: false, verdict: ["other"], verdict_label: "Another contact",
    verdict_values: { nte_7: { contact: selected.id } } }} dataProvider={{ getOne: async () => ({ data: selected }) }} />);
  await screen.findByText(selected.name);
  expect(screen.getByText(/^Chose:/).textContent).toBe("Chose: Another contact: Visible contact");
});

test("multiple placements of a question have distinct checkbox ids", () => {
  const decision = decisionFixture({ proposal: { multiple: true, alternatives: [{ key: "accept", label: "Accept", outcome: "done" }] } });
  render(<Provider><DecisionCard decision={decision} /><DecisionCard decision={decision} /></Provider>);
  const ids = screen.getAllByRole("checkbox").map((input) => input.id);
  expect(new Set(ids).size).toBe(2);
});

test("a record action uses its human alternative label", () => {
  const decision = decisionFixture({ proposal: { alternatives: [{ key: "approve", label: "Approve change", outcome: "done",
    actions: { ent_target: { record: { call: "internal_method_name" } } } }] } });
  render(<Provider><DecisionCard decision={decision} /></Provider>);
  expect(screen.getByRole("radio", { name: "Approve change" })).toBeTruthy();
  expect(screen.queryByText("internal method name")).toBeNull();
});

test("a concerned record carries its evidence tab and page into the canonical link", () => {
  const reference = { model: "storage.File", id: "fil_source", label: "Source file", tab: "preview", page: 2, search: { previewPage: "fil_source:2" } };
  const decision = decisionFixture({
    records: [{ id: "dcr_source", record_model: reference.model, record_id: reference.id }],
    context: { references: [reference] },
    proposal: { alternatives: [{ key: "accept", label: "Accept", outcome: "done" }] },
  });
  render(<Provider resources={[testDataResource("storage.File")]}><AppRuntimeProvider runtime={{
    routeHref: createRouteHref([{ name: "storage.file", path: "/storage/$id" }]),
    routesByResource: { "storage.File": { collection: "storage.files", record: { name: "storage.file", param: "id" } } },
  }}><DecisionCard decision={decision} /></AppRuntimeProvider></Provider>);
  expect(screen.getByRole("link", { name: "Source file" }).getAttribute("href")).toBe("/storage/fil_source?recordTab=preview&previewPage=fil_source%3A2");
  expect(screen.queryByRole("button", { name: "Source file" })).toBeNull();
});
