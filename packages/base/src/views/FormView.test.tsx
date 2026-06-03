// @vitest-environment happy-dom

import {
  act,
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
import { AppRuntimeProvider, type Row } from "@angee/sdk";
import { useMemo, useState, type ReactElement } from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { ModalsHost } from "../feedback";
import { defaultWidgets } from "../widgets";
import { FormView, type FormField } from "./FormView";

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
    useResourceMutation: () => [
      sdkMocks.mutate,
      { fetching: false, error: null },
    ],
  };
});

const statusOptions = [
  { value: "DRAFT", label: "Draft" },
  { value: "ACTIVE", label: "Active" },
  { value: "ARCHIVED", label: "Archived" },
];

const fields = [
  { name: "title", label: "Title", title: true },
  {
    name: "status",
    label: "Status",
    widget: "statusbar",
    options: statusOptions,
  },
  {
    name: "createdAt",
    label: "Created At",
    widget: "datetime",
    readOnly: true,
  },
  { name: "wordCount", label: "Word Count", readOnly: true },
] satisfies readonly FormField[];

describe("FormView", () => {
  afterEach(() => {
    cleanup();
  });

  beforeEach(() => {
    sdkMocks.record = {
      id: "note-1",
      title: "First",
      status: "ACTIVE",
      createdAt: "2026-05-31T12:00:00Z",
      wordCount: 3,
    };
    sdkMocks.mutate.mockReset();
    sdkMocks.mutate.mockImplementation(async ({ data }: { data: Row }) => ({
      ...sdkMocks.record,
      ...data,
    }));
  });

  test("submits only changed writable fields for an update", async () => {
    renderForm("note-1");

    const title = await screen.findByLabelText("Title");
    await waitFor(() =>
      expect((title as HTMLInputElement).value).toBe("First"),
    );

    fireEvent.change(title, { target: { value: "Renamed" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect(sdkMocks.mutate).toHaveBeenCalledWith({
      data: { title: "Renamed", id: "note-1" },
    });
  });

  test("includes an enum field when the user changes it", async () => {
    renderForm("note-1");

    await waitFor(() =>
      expect((screen.getByLabelText("Title") as HTMLInputElement).value).toBe(
        "First",
      ),
    );
    fireEvent.click(screen.getByText("Archived"));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect(sdkMocks.mutate).toHaveBeenCalledWith({
      data: { status: "ARCHIVED", id: "note-1" },
    });
  });

  test("omits unselected option fields from create payloads", async () => {
    renderForm(null);

    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect(sdkMocks.mutate).toHaveBeenCalledWith({
      data: { title: "" },
    });
  });

  test("keeps saved values after a parent re-render with new field descriptors", async () => {
    function Harness(): ReactElement {
      const [saveVersion, setSaveVersion] = useState(0);
      const viewFields = useMemo(() => cloneFields(fields), [saveVersion]);

      return (
        <>
          <span data-testid="save-version" hidden>
            {saveVersion}
          </span>
          <FormView
            model="notes.Note"
            id="note-1"
            fields={viewFields}
            onSaved={() => setSaveVersion((current) => current + 1)}
          />
        </>
      );
    }

    renderWithProviders(<Harness />);

    const title = await screen.findByLabelText("Title");
    await waitFor(() =>
      expect((title as HTMLInputElement).value).toBe("First"),
    );

    fireEvent.change(title, { target: { value: "Renamed" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(screen.getByTestId("save-version").textContent).toBe("1"),
    );
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Save" })).toBeNull(),
    );
    await act(async () => {
      await nextTask();
    });

    expect((screen.getByLabelText("Title") as HTMLInputElement).value).toBe(
      "Renamed",
    );
    expect(screen.queryByRole("button", { name: "Discard" })).toBeNull();
  });
});

function renderForm(id: string | null): void {
  renderWithProviders(<FormView model="notes.Note" id={id} fields={fields} />);
}

function renderWithProviders(children: ReactElement): void {
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

  render(
    <RouterContextProvider router={router}>
      <ModalsHost>
        <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
          {children}
        </AppRuntimeProvider>
      </ModalsHost>
    </RouterContextProvider>,
  );
}

function cloneFields(source: readonly FormField[]): FormField[] {
  return source.map((field) => ({ ...field }));
}

function nextTask(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0));
}
