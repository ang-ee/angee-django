// @vitest-environment happy-dom
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import type { MutationFilters } from "@tanstack/react-query";
import { parse } from "graphql";
import type { ReactNode } from "react";
import { afterEach, expect, test, vi } from "vitest";

import { OperationDocumentsProvider } from "../operation-documents";
import { createRefineTestProviders } from "../testing";
import { ActiveDataProviderNameProvider } from "./data-provider-context";
import { useActionMutation } from "./hooks";

const ACTION = parse("mutation Replace($id: ID!, $password: String!) { run_probe(id: $id, password: $password) { ok message validation_errors } }");
const { Provider, createClient, clearClients } = createRefineTestProviders({ providerNames: ["console"] });
afterEach(() => { cleanup(); clearClients(); });

test.each(["success", "validation", "transport"])("transient action %s clears native secret state and preserves the outcome", async (mode) => {
  const secret = "action-input-sentinel";
  const payload = mode === "validation"
    ? { ok: false, message: "Required.", validation_errors: { password: ["Required."] } }
    : { ok: true, message: "Replaced." };
  const custom = vi.fn(async () => {
    if (mode === "transport") throw new Error("Request failed.");
    return { data: { run_probe: payload } };
  });
  const client = createClient();
  const notify = vi.fn();
  const actionMutations = {
    predicate: ({ state }) => {
      const variables = state.variables;
      if (!variables || typeof variables !== "object" || !("values" in variables)) return false;
      const values = variables.values;
      return !!values && typeof values === "object" && "password" in values && values.password === secret;
    },
  } satisfies MutationFilters;
  let detached = false;
  const unsubscribe = client.getMutationCache().subscribe((event) => {
    if (event.type === "observerRemoved" && actionMutations.predicate(event.mutation)) detached = true;
  });
  function Wrapper({ children }: { children: ReactNode }) {
    return <Provider queryClient={client} dataProvider={{ custom }} notificationProvider={{ open: notify, close: vi.fn() }}>
      <ActiveDataProviderNameProvider name="console">
        <OperationDocumentsProvider documents={{ console: { actions: { run_probe: ACTION } } }}>
          {children}
        </OperationDocumentsProvider>
      </ActiveDataProviderNameProvider>
    </Provider>;
  }
  const { result } = renderHook(() => useActionMutation("run_probe", { transient: true, errorNotification: false }), { wrapper: Wrapper });
  await act(async () => {
    const pending = result.current[0]("record-1", { password: secret });
    if (mode === "transport") await expect(pending).rejects.toThrow("Request failed.");
    else await expect(pending).resolves.toMatchObject(mode === "validation"
      ? { ok: false, validationErrors: { password: ["Required."] } } : { ok: true, message: "Replaced." });
    expect(detached).toBe(true);
  });
  await waitFor(() => expect(client.getMutationCache().findAll(actionMutations)).toHaveLength(0));
  expect(JSON.stringify(client.getQueryCache().getAll().map(({ state }) => state))).not.toContain(secret);
  expect(result.current[1].error).toBeNull();
  expect(notify).not.toHaveBeenCalled();
  expect(custom).toHaveBeenCalledOnce();
  unsubscribe();
});
