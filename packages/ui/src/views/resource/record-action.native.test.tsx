// @vitest-environment happy-dom
import { act, cleanup, renderHook } from "@testing-library/react";
import { OperationDocumentsProvider } from "@angee/refine";
import { afterEach, expect, test, vi } from "vitest";
import type { ReactNode } from "react";

import { createUiTestProviders } from "../../testing";
import { useRecordActionMutation } from "./record-action";
import type { ActionContext } from "../page";

vi.mock("@tanstack/react-router", async (original) => ({
  ...(await original<object>()), useNavigate: () => vi.fn(),
}));
const { Provider, createClient, clearClients } = createUiTestProviders();
afterEach(() => { cleanup(); clearClients(); });

test.each([
  { settle: false, refreshOption: undefined }, { settle: false, refreshOption: false }, { settle: false, refreshOption: true },
  { settle: true, refreshOption: undefined }, { settle: true, refreshOption: false }, { settle: true, refreshOption: true },
])("uncovered native record action preserves default/custom refresh (settle=$settle refresh=$refreshOption)", async ({ settle, refreshOption }) => {
  let lastSynced = "before";
  const custom = vi.fn(async () => { lastSynced = "after"; return { data: { refresh_source: { ok: true, message: "Synced" } } }; });
  const client = createClient();
  const wrapper = ({ children }: { children: ReactNode }) => <Provider queryClient={client} dataProvider={{ custom }}>
    <OperationDocumentsProvider documents={{ console: { actions: { refresh_source: "mutation Refresh($id: String!) { refresh_source(id: $id) { ok message } }" } } }}>{children}</OperationDocumentsProvider>
  </Provider>;
  let shownLastSynced = "before";
  const refresh = vi.fn(() => { shownLastSynced = lastSynced; });
  const afterSuccess = vi.fn();
  const context: ActionContext = { record: { id: "src-1" }, values: {}, refresh,
    update: async () => null, prompt: async () => null };
  const hook = renderHook(() => useRecordActionMutation("refresh_source", {
    dataProviderName: "console", settle, afterSuccess,
    ...(refreshOption === undefined ? {} : { refresh: refreshOption }),
  }), { wrapper });
  await act(async () => { await hook.result.current[0](context); });
  expect(custom).toHaveBeenCalledOnce();
  expect(refresh).toHaveBeenCalledTimes(refreshOption === false ? 0 : 1);
  expect(shownLastSynced).toBe(refreshOption === false ? "before" : "after");
  expect(afterSuccess).toHaveBeenCalledOnce();
});
