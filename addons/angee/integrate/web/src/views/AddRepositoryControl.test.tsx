// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor, } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, test, vi } from "vitest";
import { AppRuntimeProvider, baseIcons } from "@angee/ui";

import { AddRepositoryControl } from "./AddRepositoryControl";

const baseMocks = vi.hoisted(() => ({
  bridgeOptions: [] as Array<{ value: string; label: string }>,
  refetchBridges: vi.fn(),
}));

// The mocked authored-operation surface: repo search and add mutation. VCS bridge
// options come from the shared refine-backed relation options owner.
const sdkMocks = vi.hoisted(() => ({
  search: {
    data: undefined as unknown,
    fetching: false,
    error: null as Error | null,
    refetch: vi.fn(),
  },
  lastSearchVars: null as unknown,
  addOptions: null as unknown,
  addRepository: vi.fn(),
  addState: { fetching: false, error: null as Error | null },
}));

// The hooks now receive a typed `graphql()` document (a parsed `DocumentNode`),
// not a raw query string, so the mock routes on the operation name in its AST.
function operationName(document: unknown): string {
  const definition = (document as { definitions?: ReadonlyArray<unknown> })
    ?.definitions?.[0];
  return (definition as { name?: { value?: string } })?.name?.value ?? "";
}

vi.mock("@angee/ui", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/ui")>();
  return {
    ...actual,
    useRelationOptions: () => ({
      list: { fetching: false, refetch: baseMocks.refetchBridges },
      options: baseMocks.bridgeOptions,
    }),
  };
});

vi.mock("@angee/refine", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/refine")>()),
  useAuthoredQuery: (document: unknown, variables: unknown) => {
    const name = operationName(document);
    if (name === "IntegrateSearchRepositories") {
      sdkMocks.lastSearchVars = variables;
      return sdkMocks.search;
    }
    return { data: undefined, fetching: false, error: null, refetch: vi.fn() };
  },
  useAuthoredMutation: (_document: unknown, options: unknown) => {
    sdkMocks.addOptions = options;
    return [sdkMocks.addRepository, sdkMocks.addState];
  },
}));

const VCS_ID = "VkNTSW50ZWdyYXRpb25UeXBlOjE=";

describe("AddRepositoryControl typeahead", () => {
  beforeAll(() => {
    Element.prototype.getAnimations ??= () => [];
    Object.defineProperty(globalThis, "ResizeObserver", {
      configurable: true,
      value: class ResizeObserver {
        observe(): void {}
        unobserve(): void {}
        disconnect(): void {}
      },
    });
  });

  afterEach(() => {
    cleanup();
    baseMocks.bridgeOptions = [];
    sdkMocks.search.data = undefined;
    sdkMocks.search.fetching = false;
    sdkMocks.lastSearchVars = null;
    sdkMocks.addOptions = null;
    sdkMocks.addRepository.mockReset();
  });

  test("does not search until a repository name is typed", async () => {
    baseMocks.bridgeOptions = bridgeOptions();
    sdkMocks.search.data = searchData();

    renderControl();
    fireEvent.click(screen.getByRole("button", { name: "Add repository" }));

    expect(await screen.findByText("Type a repository name to search.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /acme\/widgets/ })).toBeNull();
  });

  test("searches the chosen integration and inventories a picked candidate", async () => {
    baseMocks.bridgeOptions = bridgeOptions();
    sdkMocks.search.data = searchData();
    sdkMocks.addRepository.mockResolvedValue({
      add_repository: { id: "repo_1", org: "acme", name: "acme/widgets" },
    });

    renderControl();
    fireEvent.click(screen.getByRole("button", { name: "Add repository" }));

    // The single bridge auto-selects, so typing scopes the search to it.
    fireEvent.change(screen.getByLabelText("Repository name"), {
      target: { value: "widget" },
    });

    const candidate = await screen.findByRole("button", { name: /acme\/widgets/ });
    // The debounced search carries the picked bridge and the typed query.
    expect(sdkMocks.lastSearchVars).toEqual({
      vcsBridgeId: VCS_ID,
      query: "widget",
    });

    fireEvent.click(candidate);

    await waitFor(() =>
      expect(sdkMocks.addRepository).toHaveBeenCalledWith({
        vcsBridgeId: VCS_ID,
        name: "acme/widgets",
      }),
    );
    // Adding declares the repository model so authored hooks refresh the list.
    expect(sdkMocks.addOptions).toEqual({
      invalidateModels: ["integrate.Repository"],
    });
    expect(await screen.findByText("Added")).toBeTruthy();
  });
});

function renderControl(): ReturnType<typeof render> {
  return render(
    <AppRuntimeProvider runtime={{ icons: baseIcons }}>
      <AddRepositoryControl />
    </AppRuntimeProvider>,
  );
}

function bridgeOptions(): Array<{ value: string; label: string }> {
  return [{ value: VCS_ID, label: "github (active)" }];
}

function searchData(): unknown {
  return {
    search_repositories: [
      {
        name: "acme/widgets",
        org: "acme",
        default_branch: "main",
        visibility: "private",
        web_url: "https://github.com/acme/widgets",
      },
    ],
  };
}
