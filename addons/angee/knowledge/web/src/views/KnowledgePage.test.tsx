// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const routerMocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  params: {} as Record<string, string>,
}));

const sdkMocks = vi.hoisted(() => ({
  useAuthoredQuery: vi.fn(),
  refetch: {
    detail: vi.fn(async () => undefined),
    pages: vi.fn(async () => undefined),
    vaults: vi.fn(async () => undefined),
  },
}));

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => routerMocks.navigate,
  useParams: () => routerMocks.params,
}));

// `@angee/data`, `@angee/sdk`, and `@angee/base` symbols now all resolve from
// `@angee/ui`, so the three former module mocks fold into one (Vitest hoists one
// factory per module id): `useAuthoredQuery` + `useNamespaceT` + the render
// overrides, atop the real module.
vi.mock("@angee/ui", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/ui")>();
  return {
    ...actual,
    useAuthoredQuery: sdkMocks.useAuthoredQuery,
    useNamespaceT: (
      _namespace: string,
      messages: Record<string, string>,
    ) => (key: string, vars?: Record<string, string>) => {
      let message = messages[key] ?? key;
      for (const [name, value] of Object.entries(vars ?? {})) {
        message = message.replace(`{${name}}`, value);
      }
      return message;
    },
    EmptyState: ({ title }: { title: string }) => (
      <section data-testid="empty-state">{title}</section>
    ),
    Explorer: ({
      navigator,
      aside,
      children,
    }: {
      navigator: React.ReactNode;
      aside: React.ReactNode;
      children: React.ReactNode;
    }) => (
      <div>
        <nav data-testid="navigator">{navigator}</nav>
        <aside data-testid="aside">{aside}</aside>
        <main>{children}</main>
      </div>
    ),
    LoadingPanel: ({ message }: { message: string }) => (
      <section data-testid="loading">{message}</section>
    ),
    RelationPicker: ({
      value,
      options,
      onChange,
      onCreated,
      "aria-label": ariaLabel,
    }: {
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
    TreeView: ({
      rows,
      rowKey,
      label,
      selectedId,
      onSelect,
    }: {
      rows: readonly Record<string, string>[];
      rowKey: string;
      label: string;
      selectedId?: string;
      onSelect?: (row: Record<string, string>) => void;
    }) => (
      <div
        data-testid="tree"
        data-row-ids={rows.map((row) => row[rowKey]).join(",")}
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
    ),
    WikilinkProvider: ({ children }: { children: React.ReactNode }) => (
      <>{children}</>
    ),
    useConfirm: () => async () => true,
  };
});

vi.mock("../data/use-page-actions", () => ({
  usePageActions: () => ({
    busy: false,
    createPage: vi.fn(async () => "created-page"),
    deletePage: vi.fn(async () => undefined),
    movePage: vi.fn(),
  }),
}));

vi.mock("./BacklinksPanel", () => ({
  BacklinksPanel: () => <section data-testid="backlinks" />,
}));

vi.mock("./NewPageControl", () => ({
  NewPageControl: () => <button type="button">New page</button>,
}));

vi.mock("./PageEditor", () => ({
  PageEditor: ({ detail }: { detail: { id: string } }) => (
    <section data-testid="page-editor" data-page-id={detail.id} />
  ),
}));

import {
  KnowledgePage as KnowledgePageQuery,
  KnowledgePages,
  KnowledgeVaults,
} from "../data/documents";
import { KnowledgePage } from "./KnowledgePage";

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

    render(<KnowledgePage />);

    expect(rootPickerValue()).toBe("vault-b");
    expect(treeAttribute("data-row-ids")).toBe("page-b");
    expect(treeAttribute("data-selected")).toBe("page-b");
    expect(screen.getByTestId("page-editor").getAttribute("data-page-id")).toBe(
      "page-b",
    );
  });

  test("selecting a page navigates to its detail route", () => {
    render(<KnowledgePage />);

    fireEvent.click(screen.getByTestId("tree-row-page-a"));

    expect(routerMocks.navigate).toHaveBeenLastCalledWith({
      to: "/knowledge/page-a",
    });
  });

  test("selects an inline-created vault after the refetched options include it", () => {
    const view = render(<KnowledgePage />);

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
    view.rerender(<KnowledgePage />);

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
