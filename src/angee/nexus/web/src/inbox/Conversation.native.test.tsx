// @vitest-environment happy-dom
import { ShellPageTestProviders } from "@angee/app/testing";
import { parseFlatSearch, stringifyFlatSearch } from "@angee/app";
import { ToastProvider } from "@angee/ui";
import { createUiTestProviders } from "@angee/ui/testing";
import type { RefineTestDataProvider } from "@angee/refine/testing";
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from "@tanstack/react-router";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import type { ReactElement } from "react";
import { afterEach, expect, test, vi } from "vitest";
import { InboxConversation } from "./Conversation";
import { InboxTranscriptMatches } from "./documents";
import { inboxQuery, useInboxNavigation } from "./state";

// The transcript's window is driven by the route's anchor, so a test moves it by navigating.
vi.mock("@angee/messaging", async (importOriginal) => ({
  ...await importOriginal<typeof import("@angee/messaging")>(),
  ThreadTranscript: ({ anchor, renderWindow }: {
    anchor: string;
    renderWindow: (messages: { id: string }[], children: ReactElement) => ReactElement;
  }) => renderWindow([{ id: anchor.startsWith("message:") ? anchor.slice(8) : "m1" }], <div />),
}));

const { Provider, clearClients } = createUiTestProviders({ apiUrl: "test://nexus-conversation", providerNames: [],
  queryClientConfig: { defaultOptions: { queries: { retry: false, staleTime: Infinity } } } });
afterEach(() => { cleanup(); clearClients(); });

const COUNT = "Full conversation · 5 messages · 2 match your filters";

function fixture() {
  const dataProvider = { custom: vi.fn(async ({ meta }) => {
    const variables = meta?.gqlVariables ?? {};
    if (meta?.gqlQuery !== InboxTranscriptMatches) throw new Error("Unexpected inbox operation");
    // Only the first window of the first conversation settles; every later read stays pending.
    if (variables.thread !== "thread-one" || variables.at !== "m1") return new Promise<never>(() => undefined);
    return { data: { inbox_transcript_matches: {
      thread: { id: "thread-one", conversation_label: "Ada", modality: "DIRECT" },
      count: 2, total: 5, visible: ["m1"], previous: null, next: null,
    } } };
  }) } satisfies RefineTestDataProvider;
  const scope = { ...inboxQuery({}, "UTC"), sender: "", circle: "" };
  function Pane() {
    return <InboxConversation navigation={useInboxNavigation()} scope={scope} />;
  }
  const router = createRouter({ routeTree: createRootRoute({ component: Pane }), parseSearch: parseFlatSearch,
    stringifySearch: stringifyFlatSearch, history: createMemoryHistory({ initialEntries: ["/?thread=thread-one"] }) });
  render(<Provider dataProvider={dataProvider}><ShellPageTestProviders><ToastProvider>
    <RouterProvider router={router} />
  </ToastProvider></ShellPageTestProviders></Provider>);
  return router;
}

const pendingCount = () => screen.queryAllByRole("status").some((status) => status.textContent === "Loading inbox…");

test("a new transcript window keeps the conversation count; another conversation never shows it", async () => {
  const router = fixture();
  expect(await screen.findByText(COUNT)).toBeTruthy();

  // The window moves within the conversation: its read is pending, the count stays.
  await act(async () => router.navigate({ to: "/", search: (current: Record<string, unknown>) => ({ ...current, at: "message:m2" }) }));
  expect(screen.getByText(COUNT)).toBeTruthy();
  expect(pendingCount()).toBe(false);

  // Another conversation: the retained read names thread-one, so its count is withheld for a skeleton.
  await act(async () => router.navigate({ to: "/", search: { thread: "thread-two" } }));
  await waitFor(() => expect(screen.queryByText(COUNT)).toBeNull());
  expect(pendingCount()).toBe(true);
});
