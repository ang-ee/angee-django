// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import * as React from "react";
import type { TypedDocumentNode } from "@angee/refine";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  confirm: vi.fn(),
  danger: vi.fn(),
  mutate: vi.fn(),
  mutationOptions: [] as unknown[],
}));

vi.mock("../../feedback", () => ({
  errorMessage: (cause: unknown, fallback: string) =>
    cause instanceof Error ? cause.message : fallback,
  useConfirm: () => mocks.confirm,
  useToast: () => ({ danger: mocks.danger }),
}));

vi.mock("../../chrome/Glyph", () => ({
  Glyph: ({ name }: { name: string }) => <span data-testid={`glyph-${name}`} />,
}));

vi.mock("./authored-resource-mutation", () => ({
  useAuthoredResourceMutation: (_document: unknown, options: unknown) => {
    mocks.mutationOptions.push(options);
    return [mocks.mutate, { fetching: false, error: null }];
  },
}));

import {
  DeclaredRowActions,
  defineRowAction,
  useRowActionsController,
  useRowActionsSurface,
  type RowActionDeclaration,
  type RowActionPendingPolicy,
} from "./RowActions";

interface TestRow extends Record<string, unknown> {
  id: string;
  name: string;
  locked?: boolean;
}

interface RemoveResult {
  remove: boolean;
}

interface RemoveVariables extends Record<string, unknown> {
  id: string;
}

const REMOVE_DOCUMENT = {} as TypedDocumentNode<RemoveResult, RemoveVariables>;
const ROWS: readonly TestRow[] = [
  { id: "row-a", name: "Alpha" },
  { id: "row-b", name: "Beta" },
];

describe("declared row actions", () => {
  beforeEach(() => {
    mocks.confirm.mockReset();
    mocks.confirm.mockResolvedValue(true);
    mocks.danger.mockReset();
    mocks.mutate.mockReset();
    mocks.mutate.mockResolvedValue({ remove: true });
    mocks.mutationOptions.length = 0;
  });

  test("owns confirm, typed variables, invalidation, sole-root success, and bubbling", async () => {
    const parentClick = vi.fn();
    renderActions(defaultAction(), parentClick);

    fireEvent.click(withinRow("row-a"));

    await waitFor(() => expect(mocks.mutate).toHaveBeenCalledWith({ id: "row-a" }));
    expect(mocks.confirm).toHaveBeenCalledWith({
      title: "Remove Alpha?",
      body: "This cannot be undone.",
      confirm: "Remove",
      cancel: "Keep",
      danger: true,
    });
    expect(parentClick).not.toHaveBeenCalled();
    expect(mocks.danger).not.toHaveBeenCalled();
    expect(screen.getAllByTestId("glyph-trash")).toHaveLength(2);

    const option = mocks.mutationOptions[0] as {
      invalidateModels: readonly string[];
      shouldInvalidate: (result: unknown) => boolean;
    };
    expect(option.invalidateModels).toEqual(["test.Row"]);
    expect(option.shouldInvalidate({ remove: true })).toBe(true);
    expect(option.shouldInvalidate({ remove: null })).toBe(false);
    expect(option.shouldInvalidate(undefined)).toBe(false);
  });

  test("arms synchronously so a double click opens one confirm and runs one mutation", async () => {
    let resolveConfirm: ((accepted: boolean) => void) | undefined;
    mocks.confirm.mockReturnValue(
      new Promise<boolean>((resolve) => {
        resolveConfirm = resolve;
      }),
    );
    renderActions(defaultAction());

    const button = withinRow("row-a") as HTMLButtonElement;
    fireEvent.click(button);
    fireEvent.click(button);

    expect(button.disabled).toBe(true);
    expect(mocks.confirm).toHaveBeenCalledTimes(1);
    resolveConfirm?.(true);
    await waitFor(() => expect(mocks.mutate).toHaveBeenCalledTimes(1));
  });

  test("does not mutate when confirmation is declined", async () => {
    mocks.confirm.mockResolvedValue(false);
    renderActions(defaultAction());

    fireEvent.click(withinRow("row-a"));

    await waitFor(() => expect(mocks.confirm).toHaveBeenCalledTimes(1));
    expect(mocks.mutate).not.toHaveBeenCalled();
  });

  test("runs an authored action without a confirmation when confirm is omitted", async () => {
    const action = defineRowAction(typedActionInput(false));
    renderActions(action);

    fireEvent.click(withinRow("row-a"));

    await waitFor(() => expect(mocks.mutate).toHaveBeenCalledTimes(1));
    expect(mocks.confirm).not.toHaveBeenCalled();
  });

  test("turns a domain-false result into the declared danger toast", async () => {
    mocks.mutate.mockResolvedValue({ remove: false });
    renderActions(defaultAction((result) => result?.remove === true));

    fireEvent.click(withinRow("row-a"));

    await waitFor(() =>
      expect(mocks.danger).toHaveBeenCalledWith({
        title: "Alpha was not removed",
        description: "Could not remove Alpha.",
      }),
    );
  });

  test("surfaces a rejected mutation through the same danger-toast owner", async () => {
    mocks.mutate.mockRejectedValue(new Error("transport unavailable"));
    renderActions(defaultAction());

    fireEvent.click(withinRow("row-a"));

    await waitFor(() =>
      expect(mocks.danger).toHaveBeenCalledWith({
        title: "Alpha was not removed",
        description: "transport unavailable",
      }),
    );
  });

  test.each([
    ["active-row", true],
    ["disable-actions", false],
  ] as const)(
    "applies the %s pending policy across rows",
    async (pendingPolicy, activeShowsSpinner) => {
      let resolveMutation: ((result: RemoveResult) => void) | undefined;
      mocks.mutate.mockReturnValue(
        new Promise<RemoveResult>((resolve) => {
          resolveMutation = resolve;
        }),
      );
      renderActions(defaultAction(undefined, pendingPolicy));

      const buttons = screen.getAllByRole("button", { name: "Remove" });
      const first = buttons[0] as HTMLButtonElement;
      const second = buttons[1] as HTMLButtonElement;
      fireEvent.click(first);

      await waitFor(() => expect(first.disabled).toBe(true));
      expect(second.disabled).toBe(true);
      expect(first.hasAttribute("data-pending")).toBe(activeShowsSpinner);
      expect(second.hasAttribute("data-pending")).toBe(false);

      resolveMutation?.({ remove: true });
      await waitFor(() => expect(first.disabled).toBe(false));
      expect(second.disabled).toBe(false);
    },
  );

  test("honors row visibility and disabled projectors", () => {
    const action = defineRowAction({
      ...typedActionInput(),
      visible: (row: TestRow) => row.id === "row-a",
      disabled: (row: TestRow) => row.name === "Alpha",
    });
    renderActions(action);

    const [button] = screen.getAllByRole("button", { name: "Remove" }) as HTMLButtonElement[];
    expect(button?.disabled).toBe(true);
  });

  test("renders no wrapper when every declaration is hidden", () => {
    const action = defineRowAction({
      ...typedActionInput(),
      visible: () => false,
    });
    const { container } = renderActions(action);

    expect(container.querySelector("[data-testid='row-a']")?.children).toHaveLength(0);
  });

  test("returns null from the list-surface renderer when a row has no visible verbs", () => {
    const action = defineRowAction({
      ...typedActionInput(),
      visible: () => false,
    });
    const { container } = render(<SurfaceHarness action={action} />);

    expect(container.children).toHaveLength(0);
  });

  test("runs page-owned entries through the same cross-row pending controller", async () => {
    let resolveSelection: (() => void) | undefined;
    const onSelect = vi.fn(
      () => new Promise<void>((resolve) => {
        resolveSelection = resolve;
      }),
    );
    const action = defineRowAction<TestRow>({
      kind: "page",
      id: "edit",
      label: "Edit",
      icon: "pencil",
      variant: "ghost",
      pendingPolicy: "active-row",
      onSelect,
    });
    renderActions(action);

    const buttons = screen.getAllByRole("button", { name: "Edit" });
    fireEvent.click(buttons[0] as HTMLElement);

    await waitFor(() => expect((buttons[0] as HTMLButtonElement).disabled).toBe(true));
    expect((buttons[1] as HTMLButtonElement).disabled).toBe(true);
    expect(onSelect).toHaveBeenCalledWith(ROWS[0]);
    resolveSelection?.();
    await waitFor(() => expect((buttons[0] as HTMLButtonElement).disabled).toBe(false));
  });
});

afterEach(() => cleanup());

function defaultAction(
  succeeded?: (result: RemoveResult | undefined) => boolean,
  pendingPolicy: RowActionPendingPolicy = "active-row",
): RowActionDeclaration<TestRow> {
  return defineRowAction({
    ...typedActionInput(),
    ...(succeeded ? { succeeded } : {}),
    pendingPolicy,
  });
}

function typedActionInput(withConfirm = true) {
  return {
    kind: "authored" as const,
    id: "remove",
    label: "Remove",
    document: REMOVE_DOCUMENT,
    variables: (row: TestRow) => ({ id: row.id }),
    invalidateModels: ["test.Row"],
    ...(withConfirm
      ? {
          confirm: {
            title: (row: TestRow) => `Remove ${row.name}?`,
            body: () => "This cannot be undone.",
            confirm: () => "Remove",
            cancel: () => "Keep",
          },
        }
      : {}),
    toast: {
      title: (row: TestRow) => `${row.name} was not removed`,
      description: (row: TestRow) => `Could not remove ${row.name}.`,
    },
    icon: "trash",
    variant: "danger" as const,
    pendingPolicy: "active-row" as const,
  };
}

function ActionsHarness({
  action,
  onClick,
}: {
  action: RowActionDeclaration<TestRow>;
  onClick?: () => void;
}): React.ReactElement {
  const controller = useRowActionsController<TestRow>();
  return (
    <div onClick={onClick}>
      {ROWS.map((row) => (
        <div key={row.id} data-testid={row.id}>
          <DeclaredRowActions actions={[action]} controller={controller} row={row} />
        </div>
      ))}
    </div>
  );
}

function SurfaceHarness({
  action,
}: {
  action: RowActionDeclaration<TestRow>;
}): React.ReactElement {
  const surface = useRowActionsSurface([action]);
  return <>{surface.render(ROWS[0]!)}</>;
}

function renderActions(
  action: RowActionDeclaration<TestRow>,
  onClick?: () => void,
): ReturnType<typeof render> {
  return render(<ActionsHarness action={action} onClick={onClick} />);
}

function withinRow(rowId: string): HTMLElement {
  return screen.getByTestId(rowId).querySelector("button") as HTMLElement;
}
