// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { RecordThreadStreamSource } from "@angee/messaging";
import type { ChatterViewContext } from "@angee/ui";

const sdk = vi.hoisted(() => ({
  query: vi.fn(), refetch: vi.fn(), createNote: vi.fn(),
  stream: vi.fn(),
  calls: [] as string[],
}));

vi.mock("@angee/refine", async (original) => ({
  ...(await original<typeof import("@angee/refine")>()),
  useAuthoredQuery: sdk.query,
}));
vi.mock("@angee/ui", async (original) => ({
  ...(await original<typeof import("@angee/ui")>()),
  useAuthoredResourceMutation: () => [sdk.createNote, { fetching: false, error: null }],
}));
vi.mock("@angee/messaging", () => ({
  RecordThreadStream: ({ heading, source }: {
    heading: { label: string };
    source: RecordThreadStreamSource;
  }) => {
    sdk.stream({ heading, source });
    return <section>
    <h2>{heading.label}</h2>
    {source.kind === "children" ? <>
      {source.items.map((item) => <p key={item.id}>{item.title}: {item.body}</p>)}
      {source.items.length === 0 ? <><p>{source.empty?.title}</p><p>{source.empty?.description}</p></> : null}
      {source.createAction ? <button type="button">{source.createAction.label}</button> : null}
    </> : null}
    </section>;
  },
}));

import { RecordNotesStream, recordNotesTab } from "./RecordNotesStream";
import { KnowledgeRecordNotes, KnowledgeVault, KnowledgeVaultForResource, RECORD_BINDING_MODEL } from "./data/documents";

const target = { modelLabel: "example.Record", recordId: "rec_1" };
const context: ChatterViewContext = {
  pathname: "/records/rec_1", params: { id: "rec_1" },
  route: {
    name: "record", path: "/records/$id", viewType: "example/record", modelLabel: target.modelLabel,
    recordEdges: [RECORD_BINDING_MODEL],
  },
  view: { kind: "record", type: "example/record", sqid: target.recordId },
};

let bindingData: {
  record_knowledge_can_bind: boolean;
  record_knowledge_bindings: Array<{ id: string; page_detail: {
    id: string; title: string; created_at: string; created_by_label: string; markdown: { body: string };
  } }>;
};
let vaultPermissions: string[];

beforeEach(() => {
  bindingData = { record_knowledge_can_bind: true, record_knowledge_bindings: [{
    id: "binding_1", page_detail: { id: "pg_1", title: "First note",
      created_at: "2026-09-30T10:00:00Z", created_by_label: "Ada", markdown: { body: "First note\nDetails" } },
  }] };
  vaultPermissions = ["write"];
  sdk.query.mockReset().mockImplementation((document) => document === KnowledgeRecordNotes
    ? { data: bindingData, isPending: false, error: null, refetch: sdk.refetch }
    : document === KnowledgeVault
      ? { data: { vaults_by_pk: { id: "vlt_1", permissions: vaultPermissions } }, isPending: false, error: null }
      : { data: undefined, isPending: false, error: null });
  sdk.refetch.mockReset().mockResolvedValue({});
  sdk.stream.mockReset();
  sdk.calls.length = 0;
  sdk.createNote.mockReset().mockResolvedValue({ create_record_note: { id: "binding_new", page: "pg_new" } });
});
afterEach(cleanup);

test("lists bound pages in the shared child stream without a page conversation", () => {
  render(<RecordNotesStream target={target} role="notes" vault="vlt_1"
    heading={{ label: "Private notes", audience: "Managers" }} />);
  expect(screen.getByText(/First note: Details/)).toBeTruthy();
  expect(sdk.query).toHaveBeenCalledWith(KnowledgeRecordNotes,
    { modelLabel: target.modelLabel, recordId: target.recordId, role: "notes" },
    expect.objectContaining({ enabled: true }));
  expect(sdk.query).toHaveBeenCalledWith(KnowledgeVault, { id: "vlt_1" },
    expect.objectContaining({ enabled: true }));
});

test("composer posts one atomic note command, then refetches", async () => {
  render(<RecordNotesStream target={target} role="notes" vault="vlt_1" />);
  const source = lastChildSource();
  expect(source.items[0]).toMatchObject({
    title: "First note", body: "Details", authorLabel: "Ada", thread: false,
    audienceLabel: undefined,
  });
  expect(source.createComposer).toMatchObject({ bodyArg: "body", prompt: "Add note" });
  expect(source.createAction).toBeDefined();
  await source.createAction?.submit({ body: "New first line\nMore detail" }, { record: null, selectedIds: [] });
  source.onCreated?.();
  expect(sdk.createNote).toHaveBeenCalledExactlyOnceWith({
    model_label: target.modelLabel, record_id: target.recordId, vault: "vlt_1", role: "notes",
    body: "New first line\nMore detail", idempotency_key: expect.any(String),
  });
  expect(sdk.refetch).toHaveBeenCalledOnce();
});

test("composer is absent without record bind or vault write permission", () => {
  bindingData.record_knowledge_can_bind = false;
  render(<RecordNotesStream target={target} role="notes" vault="vlt_1" />);
  expect(lastChildSource().createAction).toBeUndefined();
  expect(sdk.query).toHaveBeenCalledWith(KnowledgeVault, { id: "vlt_1" },
    expect.objectContaining({ enabled: false }));
  cleanup();
  bindingData.record_knowledge_can_bind = true;
  vaultPermissions = [];
  render(<RecordNotesStream target={target} role="notes" vault="vlt_1" />);
  expect(lastChildSource().createAction).toBeUndefined();
});

test("empty stream keeps the host hint", () => {
  bindingData.record_knowledge_bindings = [];
  render(<RecordNotesStream target={target} role="notes" vault="vlt_1"
    heading={{ label: "Notes", hint: "Visible to the invited team" }} />);
  expect(screen.getByText("No notes yet.")).toBeTruthy();
  expect(screen.getByText("Visible to the invited team")).toBeTruthy();
});

test("the tab renders the same role-scoped stream where records carry bindings", () => {
  const tab = recordNotesTab({ label: "Notes", role: "notes",
    vault: "vlt_1", heading: { label: "Manager notes", hint: "For managers" } });
  expect(tab.sequence).toBe(40);
  const contribution = tab.content;
  expect(contribution).toMatchObject({ label: "Notes", icon: "notes" });
  expect(contribution.when?.(context)).toBe(true);
  expect(contribution.when?.({ ...context, view: { kind: "list", type: "example/record" } })).toBe(false);
  expect(contribution.when?.({ ...context, route: { ...context.route!, recordEdges: [] } })).toBe(false);
  expect(recordNotesTab({ label: "Notes", role: "notes", vault: "vlt_1", when: () => false }).content.when?.(context)).toBe(false);
  render(<>{contribution.render?.(context)}</>);
  expect(screen.getByText("Manager notes")).toBeTruthy();
  expect(screen.getByText(/First note: Details/)).toBeTruthy();
});

function lastChildSource(): Extract<RecordThreadStreamSource, { kind: "children" }> {
  const source = sdk.stream.mock.calls.at(-1)?.[0]?.source as RecordThreadStreamSource;
  if (source.kind !== "children") throw new Error("Expected a child stream");
  return source;
}

test("a transport retry reuses its receipt key; changing the draft starts a new request", async () => {
  sdk.createNote.mockRejectedValue(new Error("Offline"));
  render(<RecordNotesStream target={target} role="notes" vault="vlt_1" />);
  const submit = lastChildSource().createAction!.submit;
  const context = { record: null, selectedIds: [] };
  await expect(submit({ body: "Draft" }, context)).rejects.toThrow("Offline");
  await expect(submit({ body: "Draft" }, context)).rejects.toThrow("Offline");
  const first = sdk.createNote.mock.calls[0]![0].idempotency_key;
  expect(sdk.createNote.mock.calls[1]![0].idempotency_key).toBe(first);
  await expect(submit({ body: "Changed" }, context)).rejects.toThrow("Offline");
  expect(sdk.createNote.mock.calls[2]![0].idempotency_key).not.toBe(first);
});

test("notes destinations use canonical resource identity instead of a display name", () => {
  sdk.query.mockImplementation((document) => document === KnowledgeRecordNotes
    ? { data: bindingData, isPending: false, error: null }
    : document === KnowledgeVaultForResource
      ? { data: { knowledge_vault_for_resource: { id: "vlt_declared", permissions: ["write"] } }, error: null }
      : { data: undefined, error: null });
  render(<RecordNotesStream target={target} role="notes" vault={{ resource: "example.notes.destination" }} />);
  expect(sdk.query).toHaveBeenCalledWith(KnowledgeVaultForResource, { reference: "example.notes.destination" },
    expect.objectContaining({ enabled: true }));
  expect(lastChildSource().createAction?.record).toMatchObject({ id: "vlt_declared" });
});
