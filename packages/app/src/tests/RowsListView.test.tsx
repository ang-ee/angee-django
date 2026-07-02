// @vitest-environment happy-dom

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import {
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
import type { ReactElement } from "react";
import { afterEach, describe, expect, test, vi } from "vitest";

import { parseFlatSearch, stringifyFlatSearch } from "../create-app";
import { ResourceViewProvider } from "@angee/ui/views/resource-view-context";
import { RowsListView } from "@angee/ui/views/RowsListView";
import type { ListColumn } from "@angee/ui/views/resource-view-list-body";

afterEach(() => cleanup());

interface Item extends Record<string, unknown> {
  id: string;
  name: string;
  region: string;
  provider?: {
    id: string;
    name: string;
  };
}

const ROWS: Item[] = [
  { id: "1", name: "Alpha", region: "East" },
  { id: "2", name: "Beta", region: "East" },
  { id: "3", name: "Gamma", region: "West" },
];

// Only the name column is shown, so "East"/"West" appear solely as group headers
// (never as row cells) — the assertions stay unambiguous across expand/collapse.
const columns: readonly ListColumn<Item>[] = [{ field: "name", header: "Name" }];

function renderInRouter(ui: ReactElement, initialEntries = ["/"]): void {
  const rootRoute = createRootRoute();
  const indexRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/",
    component: () => ui,
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([indexRoute]),
    history: createMemoryHistory({ initialEntries }),
    parseSearch: parseFlatSearch,
    stringifySearch: stringifyFlatSearch,
  });
  render(<RouterProvider router={router} />);
}

// These assert the TanStack grouped-row collapse semantics and the
// `aria-expanded` toggle through the render output. Under happy-dom the
// virtualizer reports zero items, so `FlatListBody` takes its "render all"
// fallback; this does not exercise the virtualized count/padding.
describe("RowsListView grouping", () => {
  test("groups are collapsed by default and a header click expands only that group", async () => {
    renderInRouter(
      <RowsListView<Item>
        rows={ROWS}
        columns={columns}
        defaultGroup={{ field: "region" }}
      />,
    );

    // Both group headers render; their bodies do not (collapsed by default).
    const east = (await screen.findByText("East")).closest("button");
    const west = screen.getByText("West").closest("button");
    expect(east?.getAttribute("aria-expanded")).toBe("false");
    expect(west?.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByText("Alpha")).toBeNull();
    expect(screen.queryByText("Gamma")).toBeNull();

    // Expanding East reveals only its rows; West stays collapsed.
    fireEvent.click(east as HTMLElement);
    expect(east?.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText("Alpha")).toBeTruthy();
    expect(screen.getByText("Beta")).toBeTruthy();
    expect(screen.queryByText("Gamma")).toBeNull();

    // Toggling it again collapses it back.
    fireEvent.click(east as HTMLElement);
    expect(east?.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByText("Alpha")).toBeNull();
  });
});

describe("RowsListView filters", () => {
  test("exposes caller-declared local row fields through custom filters", async () => {
    renderInRouter(
      <RowsListView<Item>
        rows={ROWS}
        columns={columns}
        customFilterFields={[
          {
            id: "region",
            field: "region",
            label: "Region",
            type: "selection",
          },
        ]}
      />,
    );

    fireEvent.click(
      await screen.findByRole("button", { name: "Filter and favorites" }),
    );
    expect(screen.getByText("No filters")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "East" })).toBeNull();

    fireEvent.click(
      await screen.findByRole("button", { name: "Add custom filter" }),
    );
    expect(screen.getByLabelText("Filter field").textContent).toContain("Region");
    expect(screen.getByLabelText("Filter operator").textContent).toContain("is");

    fireEvent.change(screen.getByLabelText("Filter value"), {
      target: { value: "East" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));

    expect(screen.getByText("Alpha")).toBeTruthy();
    expect(screen.getByText("Beta")).toBeTruthy();
    await waitFor(() => expect(screen.queryByText("Gamma")).toBeNull());
  });

  test("matches relation public-id lookup filters against local row objects", async () => {
    renderInRouter(
      <ResourceViewProvider initialState={{ filter: { provider: { sqid: "ipr_anthropic" } } }}>
        <RowsListView<Item>
          rows={[
            {
              id: "1",
              name: "Claude",
              region: "West",
              provider: { id: "ipr_anthropic", name: "Anthropic" },
            },
            {
              id: "2",
              name: "GPT",
              region: "West",
              provider: { id: "ipr_openai", name: "OpenAI" },
            },
          ]}
          columns={columns}
        />
      </ResourceViewProvider>,
    );

    expect(await screen.findByText("Claude")).toBeTruthy();
    expect(screen.queryByText("GPT")).toBeNull();
  });

  test("standalone rows use route-owned resource-view state", async () => {
    const routeFilter = encodeURIComponent(
      JSON.stringify({ provider: { sqid: "ipr_anthropic" } }),
    );
    renderInRouter(
      <RowsListView<Item>
        rows={[
          {
            id: "1",
            name: "Claude",
            region: "West",
            provider: { id: "ipr_anthropic", name: "Anthropic" },
          },
          {
            id: "2",
            name: "GPT",
            region: "West",
            provider: { id: "ipr_openai", name: "OpenAI" },
          },
        ]}
        columns={columns}
      />,
      [`/?filter=${routeFilter}`],
    );

    expect(await screen.findByText("Claude")).toBeTruthy();
    expect(screen.queryByText("GPT")).toBeNull();
  });

  test("local scope ignores an ambient data view filter", async () => {
    renderInRouter(
      <ResourceViewProvider initialState={{ filter: { provider: { sqid: "ipr_anthropic" } } }}>
        <RowsListView<Item>
          scope="local"
          rows={[
            {
              id: "1",
              name: "Claude",
              region: "West",
              provider: { id: "ipr_anthropic", name: "Anthropic" },
            },
            {
              id: "2",
              name: "GPT",
              region: "West",
              provider: { id: "ipr_openai", name: "OpenAI" },
            },
          ]}
          columns={columns}
        />
      </ResourceViewProvider>,
    );

    expect(await screen.findByText("Claude")).toBeTruthy();
    expect(screen.getByText("GPT")).toBeTruthy();
  });
});

describe("RowsListView selection", () => {
  test("renders caller bulk actions for selected local rows", async () => {
    const action = vi.fn();
    renderInRouter(
      <RowsListView<Item>
        rows={ROWS}
        columns={columns}
        selectable
        bulkActions={(selectedIds) => (
          <button type="button" onClick={() => action([...selectedIds])}>
            Archive selected
          </button>
        )}
      />,
    );

    fireEvent.click(
      (await screen.findAllByLabelText("Select row"))[0] as HTMLElement,
    );
    fireEvent.click(screen.getByRole("button", { name: "Archive selected" }));

    expect(action).toHaveBeenCalledWith(["1"]);
  });

  test("switches to gallery cards without losing row selection", async () => {
    renderInRouter(
      <RowsListView<Item>
        rows={ROWS}
        columns={columns}
        selectable
        gallery={{ title: "name", subtitle: "region" }}
      />,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Grid view" }));
    fireEvent.click(await screen.findByLabelText("Select Alpha"));

    expect(screen.getByText("1 selected")).toBeTruthy();
  });
});
