// @vitest-environment happy-dom

import * as React from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { MutationDialogProps } from "@angee/ui";

const mocks = vi.hoisted(() => ({
  dialog: null as MutationDialogProps<{ name: string }> | null,
  createPersonal: vi.fn(),
}));

vi.mock("@tanstack/react-router", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@tanstack/react-router")>()),
  useNavigate: () => vi.fn(),
}));
vi.mock("@angee/metadata", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/metadata")>()),
  useModelMetadata: () => undefined,
}));
vi.mock("@angee/ui", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/ui")>()),
  Page: ({ children }: { children: React.ReactNode }) => children,
  PageBody: () => null,
  PageHeader: ({ actions }: { actions: React.ReactNode }) => actions,
  MutationDialog: (props: MutationDialogProps<{ name: string }>) => {
    mocks.dialog = props;
    return null;
  },
  useDashboardRegistry: () => ({ definitions: {}, store: { useCatalogue: () => ({
    summaries: [], loading: false, error: null, refresh: vi.fn(), createPersonal: mocks.createPersonal,
  }) } }),
  useRouteHref: () => () => "/dashboards",
}));

import { DashboardCataloguePage } from "./DashboardCataloguePage";

let root: Root;
let host: HTMLDivElement;
beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  mocks.dialog = null;
  mocks.createPersonal.mockReset();
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  await React.act(async () => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

test("the create dialog is one draft: it declares its scope and sends the key the dialog holds, not a new one per attempt", async () => {
  await React.act(async () => root.render(<DashboardCataloguePage />));
  const dialog = mocks.dialog!;
  expect(dialog.idempotencyScope).toBe("dashboards.create-personal");
  if (!dialog.mutation) throw new Error("The create dialog runs a mutation.");
  await dialog.mutation({ name: "Q4" }, { errorNotification: false }, "draft-key");
  await dialog.mutation({ name: "Q4" }, { errorNotification: false }, "draft-key");
  expect(mocks.createPersonal.mock.calls.map(([input]) => input.idempotencyKey)).toEqual(["draft-key", "draft-key"]);
});
