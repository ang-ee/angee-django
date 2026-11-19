// @vitest-environment happy-dom
import { act, cleanup, renderHook } from "@testing-library/react";
import { createMemoryHistory, createRootRoute, createRouter, RouterContextProvider } from "@tanstack/react-router";
import * as React from "react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { ActionFormDialog, ToastProvider, type ListViewProps } from "@angee/ui";
import { createUiTestProviders } from "@angee/ui/testing";

import { RecordNeedsPane } from "./RecordNeedsPane";

const { Provider, clearClients } = createUiTestProviders();
afterEach(() => { cleanup(); clearClients(); });

const mutation = vi.hoisted(() => ({ convert: vi.fn(), options: vi.fn() }));
vi.mock("@angee/ui", async () => ({
  ...await vi.importActual<typeof import("@angee/ui")>("@angee/ui"),
  useResourceRecordHrefLookup: () => () => undefined,
  useActionOutcomeMutation: (field: string, options: unknown) => {
    mutation.options(field, options);
    return [mutation.convert, { fetching: false }];
  },
}));

beforeEach(() => {
  vi.clearAllMocks();
  mutation.convert.mockResolvedValue({ ok: true, message: "Converted.", id: "tsk_new" });
});

test("Convert to task appears only for writable unconverted needs and collects a queue for the selected row", async () => {
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  const { result } = renderHook(() => RecordNeedsPane({ targetModel: "projects.Project", targetField: "project", targetId: "prj_1" }), {
    wrapper: ({ children }) => <Provider><RouterContextProvider router={router}><ToastProvider>{children}</ToastProvider></RouterContextProvider></Provider>,
  });
  const children = () => React.Children.toArray((result.current as React.ReactElement<{ children: React.ReactNode }>).props.children);
  const list = children()[0];
  if (!React.isValidElement<ListViewProps>(list)) throw new Error("Expected the needs list");
  expect(list.props.fields).toEqual(expect.arrayContaining(["task.id", "permissions"]));
  const action = list.props.rowActions?.find(({ id }) => id === "convert-need-to-task");
  if (!action || action.kind !== "page") throw new Error("Expected the conversion action form");
  expect(action.primary).toBe(true);
  const row = { id: "need_1", task: null, permissions: ["write"] };
  expect(action.visible(row)).toBe(true);
  expect(action.visible({ ...row, task: { id: "tsk_existing" } })).toBe(false);
  expect(action.visible({ ...row, permissions: [] })).toBe(false);
  expect(action.visible({ id: "need_1", permissions: ["write"] })).toBe(false);
  await act(async () => { await action.onSelect(row); });
  const dialog = children()[1];
  if (!React.isValidElement<React.ComponentProps<typeof ActionFormDialog>>(dialog)) throw new Error("Expected the action dialog");
  expect(dialog.props.action.args).toEqual([{ name: "queue", label: "Queue", argKind: "relation", resource: "work.Queue" }]);
  await dialog.props.action.submit?.({ queue: "que_1" }, dialog.props.context);
  expect(mutation.convert).toHaveBeenCalledWith("need_1", { queue: "que_1" });
  expect(mutation.options).toHaveBeenCalledWith("convert_need_to_task", expect.objectContaining({
    idArgument: "need", invalidateModels: ["intake.Need", "projects.Task"],
  }));
});
