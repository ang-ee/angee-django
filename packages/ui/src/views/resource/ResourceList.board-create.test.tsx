// @vitest-environment happy-dom

import { act, cleanup, render as rtlRender } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactElement, ReactNode } from "react";

import type { FormViewProps } from "../form/FormView";
import type { ListViewProps } from "./resource-view-types";

// ResourceList owns what happens after a create saves; mock its heavy children
// so the only thing exercised is that seam.
const captured = vi.hoisted(() => ({
  onSaved: undefined as FormViewProps["onSaved"],
}));

vi.mock("@tanstack/react-router", () => ({
  useSearch: () => ({}),
  useNavigate: () => vi.fn(),
}));

vi.mock("./ListView", () => ({
  ListView: (_props: ListViewProps) => null,
}));

vi.mock("../form/FormView", () => ({
  FormView: (props: FormViewProps) => {
    captured.onSaved = props.onSaved;
    return null;
  },
}));

vi.mock("./useBulkDelete", () => ({
  useBulkDelete: () => ({
    canDelete: false,
    isPending: false,
    isPreviewOpen: false,
    previewState: null,
    previewRecordCount: 0,
    previewBlockedRecordCount: 0,
    previewOverflowCount: 0,
    deleteInitiate: vi.fn(),
    onConfirm: vi.fn(),
    onCancel: vi.fn(),
  }),
}));

import { ResourceList } from "./ResourceList";

beforeEach(() => {
  captured.onSaved = undefined;
});
const clients: QueryClient[] = [];
afterEach(() => {
  cleanup();
  clients.forEach((client) => client.clear());
  clients.length = 0;
});
function render(element: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  clients.push(client);
  return rtlRender(element, {
    wrapper: ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  });
}

describe("ResourceList create on a board", () => {
  test("stays on the board after creating a card", () => {
    const onSelect = vi.fn();
    const onClose = vi.fn();
    render(
      <ResourceList
        resource="agents.InferenceProvider"
        columns={[]}
        defaultView="board"
        formFields={[{ name: "name" }]}
        creating
        onSelect={onSelect}
        onClose={onClose}
      />,
    );

    expect(captured.onSaved).toBeTypeOf("function");
    act(() => captured.onSaved?.({ id: "prv_new" }));

    // Selecting the created record is what took the demo off the board: a board
    // page's `onSelect` routes to the record, so the card just created is the
    // one thing no longer on screen. The create surface closes instead and the
    // new card arrives in its lane through the list's own invalidation.
    expect(onSelect).not.toHaveBeenCalled();
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  test("still opens the created record when the surface stays put", () => {
    const onSelect = vi.fn();
    const onClose = vi.fn();
    render(
      <ResourceList
        resource="agents.InferenceProvider"
        columns={[]}
        formFields={[{ name: "name" }]}
        creating
        onSelect={onSelect}
        onClose={onClose}
      />,
    );

    act(() => captured.onSaved?.({ id: "prv_new" }));

    // On a list the record surface does not navigate, so continuing into the
    // record just created is the useful thing to do.
    expect(onSelect).toHaveBeenCalledWith("prv_new");
  });
});
