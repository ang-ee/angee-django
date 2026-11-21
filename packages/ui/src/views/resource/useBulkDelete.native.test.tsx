// @vitest-environment happy-dom

import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { testDataResource } from "@angee/metadata/testing";
import { OperationDocumentsProvider } from "@angee/refine";
import { afterEach, expect, test, vi } from "vitest";

import { ToastProvider } from "../../feedback";
import { createUiTestProviders } from "../../testing";
import { useBulkAction } from "./bulk-action";
import { useBulkDelete, type UseBulkDeleteResult } from "./useBulkDelete";

const resource = testDataResource("notes.Note", { roots: { deletePreview: "notes_delete" } });
const documents = { console: { deletePreviews: { "notes.Note": { kind: "Document", definitions: [] } } } };
const preview = {
  total_deleted_count: 1, deleted: [], updated: [], blocked: [], refusals: [], has_blockers: false,
  root: { label: "note", object_label: "Note", object_id: "one", children: [] },
};
const { Provider, clearClients } = createUiTestProviders({
  resources: [resource],
  queryClientConfig: { defaultOptions: { queries: { retry: false }, mutations: { retry: false } } },
});
afterEach(() => { cleanup(); clearClients(); });

test("a runner given a reason refuses to run, so no verb acts on the rows while every match is selected", async () => {
  const custom = vi.fn(async () => ({ data: { notes_delete: preview } }));
  const perform = vi.fn(async () => undefined);
  let bulk!: ReturnType<typeof useBulkAction>;
  let deletion!: UseBulkDeleteResult;
  function Record({ reason }: { reason: string | null }) {
    bulk = useBulkAction(resource, vi.fn(), reason);
    deletion = useBulkDelete(resource.modelLabel, new Set(["one"]), bulk);
    return null;
  }
  const tree = (reason: string | null) => <Provider dataProvider={{ custom }}><OperationDocumentsProvider documents={documents}>
    <ToastProvider><Record reason={reason} /></ToastProvider>
  </OperationDocumentsProvider></Provider>;
  const mounted = render(tree("Every note is selected, not these rows."));
  expect(bulk.reason).toEqual({ disabledReason: "Every note is selected, not these rows." });
  await act(async () => {
    deletion.deleteInitiate();
    await bulk.run({ ids: ["one"], perform, done: () => "Done", failed: () => "Failed" });
  });
  expect(custom).not.toHaveBeenCalled();
  expect(perform).not.toHaveBeenCalled();
  // Without a reason it runs again.
  mounted.rerender(tree(null));
  await act(async () => { await bulk.run({ ids: ["one"], perform, done: () => "Done", failed: () => "Failed" }); });
  expect(perform).toHaveBeenCalledWith("one");
});

test.each(["preview", "success", "refusal", "failure"])("a late delete %s after unmount cannot close the record or notify", async (outcome) => {
  let finish!: () => void;
  const pending = new Promise<void>((resolve) => { finish = resolve; });
  const onSucceeded = vi.fn();
  const custom = vi.fn(async ({ payload }: { payload?: { confirm?: boolean } }) => {
    if (outcome === "preview" || payload?.confirm) {
      await pending;
      if (outcome === "failure") throw new Error("Late failure.");
    }
    return { data: { notes_delete: outcome === "refusal" && payload?.confirm
      ? { ...preview, has_blockers: true, refusals: ["Late refusal."] } : preview } };
  });
  let deletion!: UseBulkDeleteResult;
  function Record() {
    const bulk = useBulkAction(resource, onSucceeded);
    deletion = useBulkDelete(resource.modelLabel, new Set(["one"]), bulk);
    return null;
  }
  function Tree({ active }: { active: boolean }) {
    return <Provider dataProvider={{ custom }}><OperationDocumentsProvider documents={documents}>
      <ToastProvider>{active ? <Record /> : null}</ToastProvider>
    </OperationDocumentsProvider></Provider>;
  }
  const mounted = render(<Tree active />);
  act(() => deletion.deleteInitiate());
  await waitFor(() => expect(custom).toHaveBeenCalledTimes(1));
  if (outcome !== "preview") {
    await waitFor(() => expect(deletion.isPreviewOpen).toBe(true));
    act(() => deletion.onConfirm());
    await waitFor(() => expect(custom).toHaveBeenCalledTimes(2));
  }
  mounted.rerender(<Tree active={false} />);
  await act(async () => { finish(); await pending; });
  expect(onSucceeded).not.toHaveBeenCalled();
  expect(screen.queryByRole("button", { name: "Dismiss notification" })).toBeNull();
});
