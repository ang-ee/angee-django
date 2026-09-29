// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const sdk = vi.hoisted(() => ({ query: vi.fn(), bind: vi.fn(), unbind: vi.fn() }));

vi.mock("@angee/refine", async (original) => ({
  ...(await original<typeof import("@angee/refine")>()),
  useAuthoredQuery: sdk.query,
  useAuthoredMutation: (document: unknown) => [document === KnowledgeBindRecord ? sdk.bind : sdk.unbind],
}));
vi.mock("@angee/ui", async (original) => ({
  ...(await original<typeof import("@angee/ui")>()),
  useRuntimeViewAs: () => ({ viewAs: null, pending: false }),
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
vi.mock("./KnowledgePageView", () => ({ KnowledgePageView: ({ pageId }: { pageId: string }) => <p>Page {pageId}</p> }));

import { RecordPagesPane, recordPagesContribution } from "./RecordPagesPane";
import { KnowledgeBindRecord, KnowledgePages, KnowledgeRecordPages } from "./data/documents";
import type { ChatterViewContext } from "@angee/ui";

const context: ChatterViewContext = {
  pathname: "/records/rec_1", params: { id: "rec_1" },
  route: { name: "record", path: "/records/$id", viewType: "example/record", modelLabel: "example.Record" },
  view: { kind: "record", type: "example/record", sqid: "rec_1" },
};

beforeEach(() => {
  sdk.query.mockReset().mockImplementation((document) => document === KnowledgePages
    ? { data: { pages: [{ id: "pg_2", title: "Another", permissions: ["write"] }] }, isPending: false }
    : { data: { record_knowledge_can_bind: true, record_knowledge_bindings: [
      { id: "krb_1", page: "pg_1", page_title: "Guide", page_can_write: true, role: "reference" },
    ] }, isPending: false });
  sdk.bind.mockReset().mockResolvedValue({});
  sdk.unbind.mockReset().mockResolvedValue({});
});
afterEach(cleanup);

test("role configuration filters one shared query and opens the bound page inline", () => {
  const contribution = recordPagesContribution({ id: "references", role: "reference", when: () => true });
  expect(contribution.when?.(context)).toBe(true);
  function Count() { return <span>Count {contribution.useCount?.(context)}</span>; }
  render(<Count />);
  expect(screen.getByText("Count 1")).toBeTruthy();
  render(<RecordPagesPane context={context} role="reference" />);
  expect(sdk.query).toHaveBeenCalledWith(KnowledgeRecordPages, {
    modelLabel: "example.Record", recordId: "rec_1", role: "reference",
  }, { enabled: true, models: ["knowledge.RecordBinding"] });
  fireEvent.click(screen.getByRole("button", { name: "Guide" }));
  expect(screen.getByText("Page pg_1")).toBeTruthy();
});

test("writer controls use the role-keyed bind and unbind mutations", async () => {
  render(<RecordPagesPane context={context} role="reference" />);
  fireEvent.click(screen.getByRole("button", { name: "Bind" }));
  await waitFor(() => expect(sdk.bind).toHaveBeenCalledWith({ input: {
    model_label: "example.Record", record_id: "rec_1", page: "pg_2", role: "reference",
  } }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Unbind Guide" }).hasAttribute("disabled")).toBe(false));
  fireEvent.click(screen.getByRole("button", { name: "Unbind Guide" }));
  await waitFor(() => expect(sdk.unbind).toHaveBeenCalledWith({ input: {
    model_label: "example.Record", record_id: "rec_1", page: "pg_1", role: "reference",
  } }));
});

test("the unfiltered tab removes each binding under its stored role", async () => {
  render(<RecordPagesPane context={context} />);
  fireEvent.click(screen.getByRole("button", { name: "Unbind Guide (reference)" }));
  await waitFor(() => expect(sdk.unbind).toHaveBeenCalledWith({ input: {
    model_label: "example.Record", record_id: "rec_1", page: "pg_1", role: "reference",
  } }));
});

test("pending bindings render a skeleton without write controls", () => {
  sdk.query.mockReturnValue({ data: undefined, isPending: true });
  render(<RecordPagesPane context={context} />);
  expect(screen.getByRole("status").textContent).toContain("Loading bound pages");
  expect(screen.queryByRole("button", { name: "Bind" })).toBeNull();
});
