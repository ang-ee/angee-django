// @vitest-environment happy-dom

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import {
  RouterContextProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
import {
  AppRuntimeProvider,
  ModelMetadataProvider,
  type Row,
} from "@angee/sdk";
import { type ReactElement } from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { ModalsHost, ToastProvider } from "../feedback";
import { defaultWidgets } from "../widgets";
import { RelationPicker } from "./RelationPicker";

const sdkMocks = vi.hoisted(() => ({
  record: null as Row | null,
  mutate: vi.fn(),
}));

vi.mock("@angee/sdk", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/sdk")>();
  return {
    ...actual,
    useResourceRecord: () => ({
      record: sdkMocks.record,
      fetching: false,
      error: null,
      refetch: vi.fn(),
    }),
    useResourceMutation: () => [sdkMocks.mutate, { fetching: false, error: null }],
  };
});

const options = [
  { value: "client-1", label: "Acme OAuth" },
  { value: "client-2", label: "Globex OAuth" },
];

const editConfig = {
  model: "OAuthClient",
  fields: [{ name: "displayName", label: "Display Name", title: true }],
};

describe("RelationPicker edit affordance", () => {
  afterEach(() => cleanup());
  beforeEach(() => {
    sdkMocks.record = { id: "client-1", displayName: "Acme OAuth" };
    sdkMocks.mutate.mockReset();
  });

  test("shows the edit pencil only when a record is selected", () => {
    const { rerender } = renderPicker(
      <RelationPicker
        value={null}
        options={options}
        edit={editConfig}
        aria-label="OAuth Client"
      />,
    );
    expect(screen.queryByRole("button", { name: "Edit record" })).toBeNull();

    rerender(
      wrap(
        <RelationPicker
          value="client-1"
          options={options}
          edit={editConfig}
          aria-label="OAuth Client"
        />,
      ),
    );
    expect(screen.getByRole("button", { name: "Edit record" })).toBeTruthy();
  });

  test("hides the edit pencil when read-only", () => {
    renderPicker(
      <RelationPicker
        value="client-1"
        options={options}
        edit={editConfig}
        readOnly
        aria-label="OAuth Client"
      />,
    );
    expect(screen.queryByRole("button", { name: "Edit record" })).toBeNull();
  });

  test("opens the selected record in an edit dialog", async () => {
    renderPicker(
      <RelationPicker
        value="client-1"
        options={options}
        edit={editConfig}
        aria-label="OAuth Client"
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Edit record" }));

    // The dialog opens on the selected record (its title field is seeded).
    await screen.findByText("Edit oauthclient");
    await waitFor(() =>
      expect(
        (screen.getByLabelText("Display Name") as HTMLInputElement).value,
      ).toBe("Acme OAuth"),
    );
  });
});

function wrap(children: ReactElement): ReactElement {
  const rootRoute = createRootRoute();
  const indexRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/",
    component: () => null,
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([indexRoute]),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  return (
    <RouterContextProvider router={router}>
      <ModalsHost>
        <ToastProvider>
          <ModelMetadataProvider metadata={undefined}>
            <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
              {children}
            </AppRuntimeProvider>
          </ModelMetadataProvider>
        </ToastProvider>
      </ModalsHost>
    </RouterContextProvider>
  );
}

function renderPicker(children: ReactElement): ReturnType<typeof render> {
  return render(wrap(children));
}
