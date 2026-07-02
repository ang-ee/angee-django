// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, within, } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import {
  ChatterTabsTestHost,
  PrimaryPaneTestHost,
  ShellPageTestProviders,
} from "@angee/app/testing";

const routerMocks = vi.hoisted(() => ({
  navigate: vi.fn(), params: {} as Record<string, string>, }));

const sdkMocks = vi.hoisted(() => ({
  useAuthoredQuery: vi.fn(), refetch: {
    detail: vi.fn(async () => undefined), pages: vi.fn(async () => undefined), vaults: vi.fn(async () => undefined), }, }));

// The page now publishes a memoized navigator into the shell's primary pane via
// an effect, so its hooks must hand back stable references (matching production, // where `usePageActions` memoizes). A fresh object per render would republish
// every render and spin the publish effect. Hoist one stable actions object.
const pageActionMocks = vi.hoisted(() => ({
  busy: false, createPage: vi.fn(async () => "created-page"), deletePage: vi.fn(async () => undefined), movePage: vi.fn(), }));

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => routerMocks.navigate, useParams: () => routerMocks.params, }));

vi.mock("@angee/refine", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/refine")>()),
  useAuthoredQuery: sdkMocks.useAuthoredQuery,
}));

// The shared rendered hooks resolve from `@angee/ui`, so the module mock folds
// `useNamespaceT` and the render overrides into one Vitest-hoisted factory atop
// the real module.
vi.mock("@angee/ui", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/ui")>();
  // Production `useNamespaceT` returns a `useCallback`-stable translator; mirror
  // that here (cache by the `messages` object) so a memoized published node keeps
  // a stable identity instead of republishing every render.
  const translators = new WeakMap<
    Record<string, string>, (key: string, vars?: Record<string, string>) => string
  >();
  const makeT = (messages: Record<string, string>) => {
    let t = translators.get(messages);
    if (!t) {
      t = (key: string, vars?: Record<string, string>) => {
        let message = messages[key] ?? key;
        for (const [name, value] of Object.entries(vars ?? {})) {
          message = message.replace(`{${name}}`, value);
        }
        return message;
      };
      translators.set(messages, t);
    }
    return t;
  };
  return {
    ...actual,
    useRouteRecordId: () => routerMocks.params.id,
    useNamespaceT: (_namespace: string, messages: Record<string, string>) =>
      makeT(messages), EmptyState: ({ title }: { title: string }) => (
      <section data-testid="empty-state">{title}</section>
    ), LoadingPanel: ({ message }: { message: string }) => (
      <section data-testid="loading">{message}</section>
    ), TreeView: ({
      rows, rowKey, label, selectedId, onSelect, }: {
      rows: readonly Record<string, string>[];
      rowKey: string;
      label: string;
      selectedId?: string;
      onSelect?: (row: Record<string, string>) => void;
    }) => (
      <div
        data-testid="tree"
        data-row-ids={rows.map((row) => row[rowKey]).join(", ")}
        data-selected={selectedId ?? ""}
      >
        {rows.map((row) => (
          <button
            key={row[rowKey]}
            type="button"
            data-testid={`tree-row-${row[rowKey]}`}
            onClick={() => onSelect?.(row)}
          >
            {row[label]}
          </button>
        ))}
      </div>
    ), WikilinkProvider: ({ children }: { children: React.ReactNode }) => (
      <>{children}</>
    ), useConfirm: () => async () => true, };
});

vi.mock("../data/use-page-actions", () => ({
  usePageActions: () => pageActionMocks, }));

vi.mock("./BacklinksPanel", () => ({
  BacklinksPanel: () => <section data-testid="backlinks" />, }));

vi.mock("./NewPageControl", () => ({
  NewPageControl: () => <button type="button">New page</button>, }));

vi.mock("./PageEditor", () => ({
  PageEditor: ({ detail }: { detail: { id: string } }) => (
    <section data-testid="page-editor" data-page-id={detail.id} />
  ), }));

// The explorer pane composes RelationPicker through its own module import, so
// the picker double mocks the subpath module (same resolved id), not the barrel.
vi.mock("@angee/ui/views/RelationPicker", () => ({
  RelationPicker: ({
    value, options, onChange, onCreated, "aria-label": ariaLabel, }: {
    value?: string | null;
    options: readonly { value: string; label: string }[];
    onChange?: (value: string) => void;
    onCreated?: (value: string) => void;
    "aria-label"?: string;
  }) => (
    <div>
      <select
        aria-label={ariaLabel}
        data-testid="root-picker"
        value={value ?? ""}
        onChange={(event) => onChange?.(event.currentTarget.value)}
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      <button
        type="button"
        data-testid="create-root"
        onClick={() => {
          onChange?.("vault-created");
          onCreated?.("vault-created");
        }}
      >
        Create root
      </button>
    </div>
  ),
}));

import {
  KnowledgePage as KnowledgePageQuery,
  KnowledgePages,
  KnowledgeVaults,
} from "../data/documents";
import { KnowledgePage } from "./KnowledgePage";

function renderPage() {
  return render(
    <ShellPageTestProviders>
      <KnowledgePage />
      <PrimaryPaneTestHost />
      <ChatterTabsTestHost tabTestId={(id) => `chatter-tab-${id}`} />
    </ShellPageTestProviders>,
  );
}

let knowledgeData = makeKnowledgeData();

beforeEach(() => {
  knowledgeData = makeKnowledgeData();
  routerMocks.params = {};
  routerMocks.navigate.mockClear();
  for (const refetch of Object.values(sdkMocks.refetch)) {
    refetch.mockClear();
  }
  sdkMocks.useAuthoredQuery.mockImplementation((document) => {
    if (document === KnowledgeVaults) {
      return queryResult("vaults", {
        vaults: knowledgeData.vaults,
      });
    }
    if (document === KnowledgePages) {
      return queryResult("pages", { pages: knowledgeData.pages });
    }
    if (document === KnowledgePageQuery) {
      const pageId = routerMocks.params.id ?? "";
      return queryResult("detail", {
        pages_by_pk: knowledgeData.details[pageId] ?? null,
      });
    }
    throw new Error("Unexpected knowledge query document");
  });
});

afterEach(() => {
  cleanup();
});

describe("KnowledgePage explorer wiring", () => {
  test("uses the open page vault for a direct link", () => {
    routerMocks.params = { id: "page-b" };

    renderPage();

    expect(rootPickerValue()).toBe("vault-b");
    expect(treeAttribute("data-row-ids")).toBe("page-b");
    expect(treeAttribute("data-selected")).toBe("page-b");
    expect(screen.getByTestId("page-editor").getAttribute("data-page-id")).toBe(
      "page-b",
    );
    expect(screen.getByTestId("shell-chatter").getAttribute("data-tab-ids")).toBe(
      "backlinks",
    );
  });

  test("publishes the navigator into the primary pane", () => {
    renderPage();

    // The vault switcher + tree live in the shell's primary pane host.
    const primary = within(screen.getByTestId("shell-primary"));
    expect(primary.getByTestId("tree")).toBeTruthy();
    expect(primary.getByLabelText("Vault")).toBeTruthy();
    // Backlinks belong to an open page; the dashboard has no page-specific
    // secondary tab to publish.
    expect(screen.getByTestId("shell-chatter").getAttribute("data-tab-ids")).toBe(
      "",
    );
  });

  test("selecting a page navigates to its detail route", () => {
    renderPage();

    fireEvent.click(screen.getByTestId("tree-row-page-a"));

    expect(routerMocks.navigate).toHaveBeenLastCalledWith({
      to: "/knowledge/page-a",
    });
  });

  test("selects an inline-created vault after the refetched options include it", () => {
    const view = renderPage();

    fireEvent.click(screen.getByTestId("create-root"));

    expect(sdkMocks.refetch.vaults).toHaveBeenCalledOnce();
    expect(routerMocks.navigate).toHaveBeenLastCalledWith({ to: "/knowledge" });
    expect(rootPickerValue()).toBe("vault-a");

    knowledgeData = {
      ...knowledgeData,
      vaults: [
        ...knowledgeData.vaults,
        {
          id: "vault-created",
          name: "Created Vault",
          description: "",
          icon: null,
          accent: null,
        },
      ],
      pages: [
        ...knowledgeData.pages,
        page("created-page", "Created Page", "note", "vault-created"),
      ],
    };
    view.rerender(
      <ShellPageTestProviders>
        <KnowledgePage />
        <PrimaryPaneTestHost />
        <ChatterTabsTestHost tabTestId={(id) => `chatter-tab-${id}`} />
      </ShellPageTestProviders>,
    );

    expect(rootPickerValue()).toBe("vault-created");
    expect(treeAttribute("data-row-ids")).toBe("created-page");
  });
});

function rootPickerValue(): string {
  return (screen.getByLabelText("Vault") as HTMLSelectElement).value;
}

function treeAttribute(name: string): string | null {
  return screen.getByTestId("tree").getAttribute(name);
}

function queryResult(
  name: keyof typeof sdkMocks.refetch,
  data: Record<string, unknown>,
) {
  return {
    data,
    fetching: false,
    error: null,
    refetch: sdkMocks.refetch[name],
  };
}

function makeKnowledgeData() {
  return {
    vaults: [
      {
        id: "vault-a",
        name: "Vault A",
        description: "",
        icon: null,
        accent: null,
      },
      {
        id: "vault-b",
        name: "Vault B",
        description: "",
        icon: null,
        accent: null,
      },
    ],
    pages: [
      page("page-a", "Page A", "note", "vault-a"),
      page("page-b", "Page B", "note", "vault-b"),
    ],
    details: {
      "page-b": detail("page-b", "Page B", "vault-b"),
    } as Record<string, ReturnType<typeof detail>>,
  };
}

function page(id: string, title: string, kind: string, vault: string) {
  return {
    id,
    title,
    kind,
    icon: null,
    vault,
    parent: null,
    updated_at: "2025-01-01T00:00:00Z",
    created_by_label: "Alex",
  };
}

function detail(id: string, title: string, vault: string) {
  return {
    ...page(id, title, "note", vault),
    markdown: {
      body: "Hello",
      body_hash: "hash",
      word_count: 1,
    },
    backlinks: [],
  };
}
