// @vitest-environment happy-dom

import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { useCallback, useState } from "react";

const sdk = vi.hoisted(() => ({ query: vi.fn(), bind: vi.fn(), unbind: vi.fn(), navigate: vi.fn(), preview: { viewAs: null as string | null, pending: false } }));

vi.mock("@angee/refine", async (original) => ({
  ...(await original<typeof import("@angee/refine")>()),
  useAuthoredQuery: sdk.query,
  useAuthoredMutation: (document: unknown) => [document === KnowledgeBindRecord ? sdk.bind : sdk.unbind],
}));
vi.mock("@angee/ui", async (original) => ({
  ...(await original<typeof import("@angee/ui")>()),
  useRuntimeViewAs: () => sdk.preview,
  useRouteHref: () => (name: string, params?: { id?: string }) => `/${name.replace(".", "/")}/${params?.id ?? ""}`,
  Select: ({ options, value, onValueChange, ...props }: {
    options: readonly { value: string; label: string }[];
    value: string;
    onValueChange: (value: string) => void;
    "aria-label": string;
  }) => <select aria-label={props["aria-label"]} value={value}
    onChange={(event) => onValueChange(event.currentTarget.value)}>
    {options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
  </select>,
}));

import { pageBacklinksGroup, pageBoundToGroup, recordPagesGroup } from "./RecordPagesPane";
import { KnowledgeBindRecord, KnowledgePage, KnowledgePageRecordBindings, KnowledgePages, KnowledgeRecordPages } from "./data/documents";
import { AppRuntimeProvider, CHATTER_CONTAINERS, InAppLinkProvider, RelatedPane, RelatedCountProbe, containersFromChildren, createRouteHref, type ChatterViewContext, type ContainerChild, type RelatedGroupContent } from "@angee/ui";

import { createUiTestProviders } from "@angee/ui/testing";
import { testDataResource, testQueryField, testResourceQuery } from "@angee/metadata/testing";

const { Provider, clearClients } = createUiTestProviders({
  resources: [testDataResource("example.Record", {
    recordRepresentation: "title", query: testResourceQuery({ fields: { id: testQueryField("id"), title: testQueryField("title") } }),
  })],
});
const context: ChatterViewContext = {
  pathname: "/records/rec_1", params: { id: "rec_1" },
  route: {
    name: "record", path: "/records/$id", viewType: "example/record", modelLabel: "example.Record",
    recordEdges: ["knowledge.RecordBinding"],
  },
  view: { kind: "record", type: "example/record", sqid: "rec_1" },
};

function Count({ current }: { current: ChatterViewContext }) {
  const [count, setCount] = useState<number>();
  const publish = useCallback((_: string, next: number | undefined) => setCount(next), []);
  return <><RelatedCountProbe id="related" context={current} onCount={publish} /><span>Related {count}</span></>;
}
function Groups({ groups, current = context }: {
  groups: Record<string, ContainerChild<RelatedGroupContent>>; current?: ChatterViewContext;
}) {
  return <Provider dataProvider={{ getList: async () => ({ data: [{ id: "rec_1", title: "Record rec_1" }], total: 1 }) }}>
    <InAppLinkProvider navigate={sdk.navigate}><AppRuntimeProvider runtime={{
      routeHref: createRouteHref([{ name: "records", path: "/records" }, { name: "record", path: "/records/$id" }]),
      routesByResource: { "example.Record": { collection: "records", record: { name: "record", param: "id" } } },
      containers: containersFromChildren(CHATTER_CONTAINERS, { [`${current.route?.modelLabel}#groups`]: groups }) }}>
    <Count current={current} /><RelatedPane context={current} />
  </AppRuntimeProvider></InAppLinkProvider></Provider>;
}

beforeEach(() => {
  sdk.query.mockReset().mockImplementation((document) => document === KnowledgePageRecordBindings
    ? { data: { page_record_bindings: [{ id: "krb_1", model_label: "example.Record", record_id: "rec_1", role: "contract" }] }, isPending: false }
    : document === KnowledgePages
    ? { data: { pages: [{ id: "pg_2", title: "Another", permissions: ["write"] }] }, isPending: false }
    : document === KnowledgePage
      ? { data: { pages_by_pk: { backlinks: [{ page: "pg_9", title: "Linking page", display_text: "see also" }] } }, isPending: false }
      : { data: { record_knowledge_can_bind: true, record_knowledge_bindings: [
        { id: "krb_1", page: "pg_1", page_title: "Guide", page_can_write: true, role: "reference" },
      ] }, isPending: false });
  sdk.preview = { viewAs: null, pending: false };
  sdk.bind.mockReset().mockResolvedValue({});
  sdk.unbind.mockReset().mockResolvedValue({});
  sdk.navigate.mockReset();
});
afterEach(() => { cleanup(); clearClients(); });

test("role configuration filters one shared query and each row links to the page reader", () => {
  const group = recordPagesGroup({ label: "References", role: "reference", sequence: 45, when: () => true });
  expect(group).toMatchObject({ sequence: 45, content: { label: "References" } });
  const contribution = group.content;
  expect(contribution.when?.(context)).toBe(true);
  expect(contribution.edge).toEqual({ model: "knowledge.RecordBinding" });
  expect(recordPagesGroup({ when: () => false }).content.when?.(context)).toBe(false);
  render(<Groups groups={{ references: group }} />);
  expect(screen.getByText("Related 1")).toBeTruthy();
  expect(sdk.query).toHaveBeenCalledWith(KnowledgeRecordPages, {
    modelLabel: "example.Record", recordId: "rec_1", role: null,
  }, { enabled: true, models: ["knowledge.RecordBinding"] });
  const row = screen.getByRole("link", { name: "Guide" });
  expect(row.getAttribute("href")).toBe("/knowledge/page/pg_1");
  fireEvent.click(row);
  expect(sdk.navigate).toHaveBeenCalledWith("/knowledge/page/pg_1");
});

test("writer controls use the role-keyed bind and unbind mutations", async () => {
  let finishBinding!: () => void;
  sdk.bind.mockImplementation(() => new Promise<void>((resolve) => { finishBinding = resolve; }));
  render(<Groups groups={{ pages: recordPagesGroup({ role: "reference" }) }} />);
  fireEvent.click(screen.getByRole("button", { name: "Bind" }));
  const dialog = await screen.findByRole("dialog");
  fireEvent.click(within(dialog).getByRole("button", { name: "Bind" }));
  await waitFor(() => expect(sdk.bind).toHaveBeenCalledWith({ input: {
    model_label: "example.Record", record_id: "rec_1", page: "pg_2", role: "reference",
  } }));
  expect(screen.getByRole("button", { name: "Unbind Guide" }).hasAttribute("disabled")).toBe(true);
  await act(async () => finishBinding());
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  await waitFor(() => expect(screen.getByRole("button", { name: "Unbind Guide" }).hasAttribute("disabled")).toBe(false));
  fireEvent.click(screen.getByRole("button", { name: "Unbind Guide" }));
  await waitFor(() => expect(sdk.unbind).toHaveBeenCalledWith({ input: {
    model_label: "example.Record", record_id: "rec_1", page: "pg_1", role: "reference",
  } }));
});

test("generic Pages and role groups partition eligible bindings and the Related count", () => {
  const bindings = [
    { id: "krb_contract", page: "pg_contract", page_title: "Signed contract", page_can_write: false, role: "contract" },
    { id: "krb_related", page: "pg_related", page_title: "General notes", page_can_write: false, role: "related" },
    { id: "krb_other", page: "pg_other", page_title: "Unclaimed reference", page_can_write: false, role: "reference" },
    { id: "krb_hidden", page: null, page_title: null, page_can_write: false, role: "related" },
  ];
  sdk.query.mockReturnValue({ data: { record_knowledge_can_bind: false, record_knowledge_bindings: bindings }, isPending: false });
  const generic = recordPagesGroup();
  const contract = recordPagesGroup({ label: "Contract", role: "contract" });
  render(<Groups groups={{ generic, contract, inactive: recordPagesGroup({ role: "reference", when: () => false }) }} />);
  expect(screen.getByText("Related 3")).toBeTruthy();
  expect(screen.getAllByRole("link", { name: "Signed contract" })).toHaveLength(1);
  expect(screen.getByText("Pages").closest("li")?.textContent).toBe("Pages· 2");
  expect(screen.getByText("Contract").closest("li")?.textContent).toBe("Contract· 1");
  expect(screen.getByRole("link", { name: /Unclaimed reference/ })).toBeTruthy();
  cleanup();
  render(<Groups groups={{ generic }} />);
  expect(screen.getByText("Pages").closest("li")?.textContent).toBe("Pages· 3");
  expect(screen.getAllByRole("link", { name: /Signed contract/ })).toHaveLength(1);
  expect(screen.getByText("Related 3")).toBeTruthy();
});

test("Bound-to rows use the shared reference label and route lookup", async () => {
  const current: ChatterViewContext = { ...context, route: { ...context.route!, modelLabel: "knowledge.Page" }, view: { kind: "record", type: "knowledge/page", sqid: "pg_1" } };
  render(<Groups current={current} groups={{ "knowledge.bound-to": pageBoundToGroup }} />);
  const row = await screen.findByRole("link", { name: /Record rec_1/ });
  // The binding role is a declared id; the row never shows it raw.
  expect(row.textContent).not.toContain("contract");
  expect(row.getAttribute("href")).toBe("/records/rec_1");
  fireEvent.click(row);
  expect(sdk.navigate).toHaveBeenCalledWith("/records/rec_1");
});

test.each([pageBacklinksGroup, pageBoundToGroup])("reverse groups load before counting and disappear when empty ($content.label)", (group) => {
  sdk.query.mockReturnValue({ data: undefined, isPending: true });
  const view = render(<Groups groups={{ reverse: group }} />);
  expect(screen.getByRole("status").textContent).toContain("Loading page");
  sdk.query.mockReturnValue({ data: { pages_by_pk: { backlinks: [] }, page_record_bindings: [] }, isPending: false });
  view.rerender(<Groups groups={{ reverse: group }} />);
  expect(screen.queryByText(String(group.content.label))).toBeNull();
});

test("the unfiltered group removes each binding under its stored role", async () => {
  render(<Groups groups={{ pages: recordPagesGroup() }} />);
  fireEvent.click(screen.getByRole("button", { name: "Unbind Guide" }));
  await waitFor(() => expect(sdk.unbind).toHaveBeenCalledWith({ input: {
    model_label: "example.Record", record_id: "rec_1", page: "pg_1", role: "reference",
  } }));
});

test("pending bindings render a skeleton without write controls", () => {
  sdk.query.mockReturnValue({ data: undefined, isPending: true });
  render(<Groups groups={{ pages: recordPagesGroup() }} />);
  expect(screen.getByRole("status").textContent).toContain("Loading bound pages");
  expect(screen.queryByRole("button", { name: "Bind" })).toBeNull();
});

test("a page's backlinks are a Related group linking to the linking pages", () => {
  const pageContext: ChatterViewContext = {
    ...context, route: { ...context.route!, modelLabel: "knowledge.Page" }, view: { kind: "record", type: "knowledge/page", sqid: "pg_1" },
  };
  render(<Groups current={pageContext} groups={{ backlinks: pageBacklinksGroup }} />);
  expect(screen.getByText("Related 1")).toBeTruthy();
  expect(screen.getByRole("link", { name: /Linking page/ }).getAttribute("href")).toBe("/knowledge/page/pg_9");
});

test("Bind keeps mutation errors inside the open picker", async () => {
  sdk.bind.mockRejectedValue(new Error("Binding refused"));
  render(<Groups groups={{ pages: recordPagesGroup() }} />);
  fireEvent.click(screen.getByRole("button", { name: "Bind" }));
  const dialog = await screen.findByRole("dialog");
  fireEvent.click(within(dialog).getByRole("button", { name: "Bind" }));
  expect((await within(dialog).findByRole("alert")).textContent).toContain("Binding refused");
  expect(screen.getByRole("dialog")).toBe(dialog);
});

test.each([{ viewAs: "actor", pending: false }, { viewAs: null, pending: true }])("view-as suppresses Bind and Unbind (%j)", (preview) => {
  sdk.preview = preview;
  render(<Groups groups={{ pages: recordPagesGroup() }} />);
  expect(screen.getByRole("link", { name: /Guide/ })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Bind" })).toBeNull();
  expect(screen.queryByRole("button", { name: /Unbind/ })).toBeNull();
});

test("the generic group's Bind writes related, unless an eligible sibling owns it", async () => {
  render(<Groups groups={{ pages: recordPagesGroup() }} />);
  fireEvent.click(screen.getByRole("button", { name: "Bind" }));
  fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Bind" }));
  await waitFor(() => expect(sdk.bind).toHaveBeenCalledWith({ input: {
    model_label: "example.Record", record_id: "rec_1", page: "pg_2", role: "related",
  } }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  cleanup();
  render(<Groups groups={{ pages: recordPagesGroup(), owned: recordPagesGroup({ label: "Owned", role: "related" }) }} />);
  expect(screen.getAllByRole("button", { name: "Bind" })).toHaveLength(1);
  expect(screen.getByRole("button", { name: "Bind" }).closest("li")?.textContent).toContain("Owned");
});
