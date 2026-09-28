// @vitest-environment happy-dom

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const sdk = vi.hoisted(() => ({
  query: vi.fn(),
  updatePage: vi.fn(),
  updateBody: vi.fn(),
  invalidate: vi.fn(),
}));

vi.mock("@angee/refine", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/refine")>()),
  useAuthoredQuery: sdk.query,
  useAuthoredMutation: () => [sdk.updateBody, { fetching: false, error: null }],
  useInvalidateAuthoredModels: () => sdk.invalidate,
}));

vi.mock("@refinedev/core", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@refinedev/core")>()),
  useUpdate: () => ({
    mutateAsync: sdk.updatePage,
    mutation: { isPending: false, error: null },
  }),
}));

// Keep the real reader, editor, draft hook, buttons and error surface. Only the
// registry's markdown widget is replaced: CodeMirror belongs to its own suite.
vi.mock("@angee/ui", async (importOriginal) => {
  const preview = { read: ({ value }: { value: unknown }) => <p>{String(value)}</p> };
  const editor = {
    edit: ({ value, onChange }: { value: unknown; onChange: (value: string) => void }) => (
      <textarea
        aria-label="Page body"
        value={String(value)}
        onChange={(event) => onChange(event.currentTarget.value)}
      />
    ),
  };
  return {
    ...(await importOriginal<typeof import("@angee/ui")>()),
    useResolvedWidget: (name: string) => name === "markdown.preview" ? preview : editor,
  };
});

import { KnowledgePageView } from "./KnowledgePageView";
import { KnowledgePage, PAGE_READ_MODELS, type KnowledgePageDetail } from "./data/documents";

function detail(overrides: Partial<KnowledgePageDetail> = {}): KnowledgePageDetail {
  return {
    id: "pg_guide",
    title: "Guide",
    kind: "note",
    can_write: true,
    icon: "",
    vault: "vlt_reference",
    parent: null,
    updated_at: "2026-01-01T12:00:00Z",
    created_by_label: "Author",
    markdown: { body: "Published instructions", body_hash: "original-hash", word_count: 2 },
    backlinks: [],
    ...overrides,
  };
}

function queryResult(page: KnowledgePageDetail | null, error: Error | null = null) {
  return { data: { pages_by_pk: page }, error, isPending: false, fetching: false };
}

beforeEach(() => {
  vi.useFakeTimers();
  sdk.query.mockReset().mockReturnValue(queryResult(detail()));
  sdk.updatePage.mockReset().mockResolvedValue({ data: { id: "pg_guide", title: "Draft title" } });
  sdk.updateBody.mockReset().mockResolvedValue({
    update_page_body: { ok: true, markdown: { body_hash: "saved-hash" } },
  });
  sdk.invalidate.mockReset();
});

afterEach(async () => {
  await act(async () => cleanup());
  vi.clearAllTimers();
  vi.useRealTimers();
});

describe("KnowledgePageView", () => {
  test("reads the requested page and renders its content without page shell chrome", () => {
    render(<KnowledgePageView pageId="pg_guide" />);
    expect(sdk.query).toHaveBeenCalledWith(KnowledgePage, { id: "pg_guide" }, { models: PAGE_READ_MODELS });
    expect(screen.getByRole("heading", { name: "Guide" })).toBeTruthy();
    expect(screen.getByText("Published instructions")).toBeTruthy();
    expect(screen.queryByRole("navigation")).toBeNull();
  });

  test("a readable page without write permission has no edit control", () => {
    sdk.query.mockReturnValue(queryResult(detail({ can_write: false })));
    render(<KnowledgePageView pageId="pg_guide" />);
    expect(screen.getByRole("heading", { name: "Guide" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
    expect(screen.queryByRole("textbox")).toBeNull();
  });

  test.each(["unreadable", "absent"])("renders nothing when the server returns an %s page", (pageId) => {
    sdk.query.mockReturnValue(queryResult(null));
    const { container } = render(<KnowledgePageView pageId={pageId} />);
    expect(container.childElementCount).toBe(0);
  });

  test("shows a loading surface while the first read is pending", () => {
    sdk.query.mockReturnValue({ data: undefined, error: null, isPending: true });
    render(<KnowledgePageView pageId="pg_guide" />);
    expect(screen.getByText("Loading page")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
  });

  test("shows the error banner when a failed read has no cached data", () => {
    sdk.query.mockReturnValue({ data: undefined, error: new Error("Read failed"), isPending: false });
    render(<KnowledgePageView pageId="pg_guide" />);
    expect(screen.getByRole("alert").textContent).toContain("Read failed");
    expect(screen.queryByRole("heading")).toBeNull();
  });

  test("write permission enables title and body edits through the existing editor", async () => {
    render(<KnowledgePageView pageId="pg_guide" />);
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Page title" }), { target: { value: "Draft title" } });
    fireEvent.blur(screen.getByRole("textbox", { name: "Page title" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Page body" }), { target: { value: "Draft body" } });
    await act(async () => { await vi.advanceTimersByTimeAsync(700); });
    expect(sdk.updatePage).toHaveBeenCalledWith({ id: "pg_guide", values: { title: "Draft title" } });
    expect(sdk.updateBody).toHaveBeenCalledWith({ page: "pg_guide", body: "Draft body", expected_hash: "original-hash" });
  });

  test("focus moves into the title on Edit and returns to Edit on Done", () => {
    render(<KnowledgePageView pageId="pg_guide" />);
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    expect(document.activeElement).toBe(screen.getByRole("textbox", { name: "Page title" }));
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Edit" }));
  });

  test("a failed background refetch preserves the mounted editor and unsaved draft", () => {
    const cached = detail();
    sdk.query.mockReturnValue(queryResult(cached));
    const view = render(<KnowledgePageView pageId="pg_guide" />);
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    const title = screen.getByRole<HTMLInputElement>("textbox", { name: "Page title" });
    fireEvent.change(title, { target: { value: "Unsaved title" } });
    sdk.query.mockReturnValue(queryResult(cached, new Error("Background read failed")));
    view.rerender(<KnowledgePageView pageId="pg_guide" />);
    expect(screen.getByRole("textbox", { name: "Page title" })).toBe(title);
    expect(title.value).toBe("Unsaved title");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(sdk.updatePage).not.toHaveBeenCalled();
  });

  test("write revocation exits editing and does not reopen when write returns", () => {
    const view = render(<KnowledgePageView pageId="pg_guide" />);
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    sdk.query.mockReturnValue(queryResult(detail({ can_write: false })));
    view.rerender(<KnowledgePageView pageId="pg_guide" />);
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
    sdk.query.mockReturnValue(queryResult(detail()));
    view.rerender(<KnowledgePageView pageId="pg_guide" />);
    expect(screen.getByRole("button", { name: "Edit" })).toBeTruthy();
    expect(screen.queryByRole("textbox")).toBeNull();
  });

  test("changing page identity resets the edit transition", () => {
    const view = render(<KnowledgePageView pageId="pg_guide" />);
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    sdk.query.mockReturnValue(queryResult(detail({ id: "pg_other", title: "Other page" })));
    view.rerender(<KnowledgePageView pageId="pg_other" />);
    expect(screen.getByRole("heading", { name: "Other page" })).toBeTruthy();
    expect(screen.queryByRole("textbox")).toBeNull();
  });
});
