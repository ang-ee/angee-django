// @vitest-environment happy-dom

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, test, vi } from "vitest";
import { AppRuntimeProvider } from "@angee/sdk";
import { baseIcons } from "@angee/base";

import { AddRepositoryControl } from "./AddRepositoryControl";

// The mocked SDK surface: the two reads (bridge catalogue, repo search), the
// add mutation, and the list invalidator the control fires after an add.
const sdkMocks = vi.hoisted(() => ({
  integrations: {
    data: undefined as unknown,
    fetching: false,
    error: null as Error | null,
    refetch: vi.fn(),
  },
  search: {
    data: undefined as unknown,
    fetching: false,
    error: null as Error | null,
    refetch: vi.fn(),
  },
  lastSearchVars: null as unknown,
  addRepository: vi.fn(),
  addState: { fetching: false, error: null as Error | null },
  invalidate: vi.fn(),
}));

// The hooks now receive a typed `graphql()` document (a parsed `DocumentNode`),
// not a raw query string, so the mock routes on the operation name in its AST.
function operationName(document: unknown): string {
  const definition = (document as { definitions?: ReadonlyArray<unknown> })
    ?.definitions?.[0];
  return (definition as { name?: { value?: string } })?.name?.value ?? "";
}

vi.mock("@angee/sdk", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/sdk")>();
  return {
    ...actual,
    useAuthoredQuery: (document: unknown, variables: unknown) => {
      const name = operationName(document);
      if (name === "IntegrateVcsBridges") return sdkMocks.integrations;
      if (name === "IntegrateSearchRepositories") {
        sdkMocks.lastSearchVars = variables;
        return sdkMocks.search;
      }
      return { data: undefined, fetching: false, error: null, refetch: vi.fn() };
    },
    useAuthoredMutation: () => [sdkMocks.addRepository, sdkMocks.addState],
    useModelInvalidation: () => sdkMocks.invalidate,
  };
});

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
    sdkMocks.integrations.data = undefined;
    sdkMocks.search.data = undefined;
    sdkMocks.search.fetching = false;
    sdkMocks.lastSearchVars = null;
    sdkMocks.addRepository.mockReset();
    sdkMocks.invalidate.mockReset();
  });

  test("does not search until a repository name is typed", async () => {
    sdkMocks.integrations.data = integrationsData();
    sdkMocks.search.data = searchData();

    renderControl();
    fireEvent.click(screen.getByRole("button", { name: "Add repository" }));

    expect(await screen.findByText("Type a repository name to search.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /acme\/widgets/ })).toBeNull();
  });

  test("searches the chosen integration and inventories a picked candidate", async () => {
    sdkMocks.integrations.data = integrationsData();
    sdkMocks.search.data = searchData();
    sdkMocks.addRepository.mockResolvedValue({
      addRepository: { id: "repo_1", org: "acme", name: "acme/widgets" },
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
    // Adding refreshes the repository list so the new row appears.
    await waitFor(() => expect(sdkMocks.invalidate).toHaveBeenCalled());
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

function integrationsData(): unknown {
  return {
    vcsBridges: {
      results: [{ id: VCS_ID, displayName: "github (active)" }],
    },
  };
}

function searchData(): unknown {
  return {
    searchRepositories: [
      {
        name: "acme/widgets",
        org: "acme",
        defaultBranch: "main",
        visibility: "private",
        webUrl: "https://github.com/acme/widgets",
      },
    ],
  };
}
