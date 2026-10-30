// @vitest-environment happy-dom
import { useInvalidateAuthoredModels } from "@angee/refine";
import type { ChatterViewContext } from "@angee/ui";
import { createUiTestProviders } from "@angee/ui/testing";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { RecordConversationPane } from "./RecordConversationPane";
import { useRecordConversationsCount } from "./RecordConversationsGroup";
import { RecordSourceThreadsDocument, type RecordSourceThreadRow } from "./documents";
import type { ThreadTranscriptProps } from "./ThreadTranscript";

vi.mock("./ThreadTranscript", () => ({
  ThreadTranscript: ({ threadId, order }: ThreadTranscriptProps) => <div data-testid="transcript">{threadId}:{order}</div>,
}));

const { Provider, createClient, clearClients } = createUiTestProviders();
afterEach(() => { cleanup(); clearClients(); });
const context: ChatterViewContext = {
  pathname: "/notes/nte_1", params: { id: "nte_1" },
  route: { name: "notes.record", path: "/notes/$id", viewType: "notes.Note", modelLabel: "notes.Note" },
  view: { kind: "record", type: "notes.Note", sqid: "nte_1" },
};
const attachment = (id: string, threadId: string): RecordSourceThreadRow => ({
  id, label: "", created_at: "2026-10-19T12:00:00Z",
  thread: { id: threadId, title: { text: "Conversation" }, message_count: 1, last_message_at: null },
});

test("opens the selected readable attachment's transcript and closes it when unbound", async () => {
  let edges = [attachment("tat_1", "thr_1"), attachment("tat_2", "thr_2")];
  const custom = vi.fn(async () => ({ data: { record_source_threads: edges } }));
  const client = createClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  function Read() {
    const count = useRecordConversationsCount(context);
    const invalidate = useInvalidateAuthoredModels();
    return <>
      <button onClick={() => invalidate(["messaging.ThreadAttachment"])}>{count} conversations</button>
      <RecordConversationPane context={context} attachmentId="tat_2" />
    </>;
  }
  render(<Provider queryClient={client} dataProvider={{ custom }}><Read /></Provider>);
  expect(await screen.findByTestId("transcript")).toHaveProperty("textContent", "thr_2:history");
  expect(screen.getByText("2 conversations")).toBeTruthy();
  expect(custom).toHaveBeenCalledTimes(1);
  expect(custom).toHaveBeenCalledWith(expect.objectContaining({ meta: expect.objectContaining({
    gqlQuery: RecordSourceThreadsDocument, gqlVariables: { modelLabel: "notes.Note", recordId: "nte_1" },
  }) }));
  edges = [attachment("tat_1", "thr_1")];
  fireEvent.click(screen.getByRole("button"));
  expect(await screen.findByText("This conversation is no longer available.")).toBeTruthy();
  expect(screen.queryByTestId("transcript")).toBeNull();
  expect(screen.getByText("1 conversations")).toBeTruthy();
});

test("an attachment outside the readable source edges never opens a transcript", async () => {
  const custom = vi.fn(async () => ({ data: { record_source_threads: [attachment("tat_visible", "thr_1")] } }));
  render(<Provider dataProvider={{ custom }}><RecordConversationPane context={context} attachmentId="tat_private" /></Provider>);
  await waitFor(() => expect(custom).toHaveBeenCalledTimes(1));
  expect(await screen.findByText("This conversation is no longer available.")).toBeTruthy();
  expect(screen.queryByTestId("transcript")).toBeNull();
});
