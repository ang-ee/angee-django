// @vitest-environment happy-dom
// The action bar and a verb bound with `useRecordActionMutation` together: only the transport, the
// toast sink and the router's navigate are faked, so each outcome's toasts, refresh and navigation
// are the ones a record form produces.

import { testDataResource } from "@angee/metadata/testing";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import * as React from "react";
import { afterEach, expect, test, vi } from "vitest";

import { ModalsHost } from "../../feedback";
import { AppRuntimeProvider, createRouteHref } from "../../runtime";
import { createUiTestProviders } from "../../testing";
import { useRecordActionMutation, type UseRecordActionOptions } from "../resource/record-action";
import { RecordActionBar } from "./RecordActionBar";

const fake = vi.hoisted(() => {
  const events: string[] = [];
  const tone = (name: string) => vi.fn((options: { title?: unknown; description?: unknown }) => {
    events.push(`${name}:${String(options.title)}|${String(options.description ?? "")}`);
    return "toast";
  });
  const toast = Object.assign(tone("info"), { success: tone("success"), danger: tone("danger"), info: tone("info"), warning: tone("warning"), close: vi.fn() });
  return { events, toast, mutate: vi.fn() };
});
vi.mock("@tanstack/react-router", async (importOriginal) => ({
  ...(await importOriginal<object>()), useNavigate: () => (target: { href?: string }) => { fake.events.push(`navigate:${target.href}`); },
}));
vi.mock("@angee/refine", async (importOriginal) => ({
  ...(await importOriginal<object>()), useActionMutation: () => [fake.mutate, { fetching: false, error: null }],
}));
vi.mock("../../feedback", async (importOriginal) => ({
  ...(await importOriginal<object>()), useToast: () => fake.toast,
}));

const { Provider, clearClients } = createUiTestProviders({
  resources: [testDataResource("example.Project")],
  navigate: (href: string) => { fake.events.push(`navigate:${href}`); },
  queryClientConfig: { defaultOptions: { mutations: { retry: false }, queries: { retry: false } } },
});
/** What the last `press` returned: nothing more may have happened by the end of its test. */
let pressed: readonly string[] | undefined;
afterEach(() => {
  if (pressed) expect(fake.events).toEqual(pressed);
  pressed = undefined;
  cleanup(); clearClients(); fake.events.length = 0; fake.mutate.mockReset();
});

function Bar({ options }: { options: UseRecordActionOptions }): React.ReactElement {
  const [run] = useRecordActionMutation("create_proposal_track", {
    ...options, afterSuccess: () => { fake.events.push("afterSuccess"); },
  });
  return <RecordActionBar record={{ id: "prp_1" }} reload={() => { fake.events.push("refresh"); }}
    actions={[{ id: "create-track", label: "Create track", placement: "toolbar", run }]} />;
}

/** Press the verb and return what happened, in order, once the bar has toasted it and its button has settled. */
async function press(options: UseRecordActionOptions = { linkTo: "example.Project" }): Promise<readonly string[]> {
  render(<Provider><AppRuntimeProvider runtime={{
    routesByResource: { "example.Project": { collection: "example.projects", record: { name: "example.project", param: "id" } } },
    routeHref: createRouteHref([
      { name: "example.projects", path: "/example/projects" },
      { name: "example.project", path: "/example/projects/$id" },
    ]),
  }}><ModalsHost><Bar options={options} /></ModalsHost></AppRuntimeProvider></Provider>);
  const button = screen.getByRole("button", { name: "Create track" });
  fireEvent.click(button);
  await waitFor(() => expect(fake.events.some((event) => /^(success|danger):/.test(event))).toBe(true));
  await waitFor(() => expect(button.getAttribute("aria-busy")).toBeNull());
  pressed = [...fake.events];
  return pressed;
}

test("a verb that creates a record: the bar follows it, refreshes, then toasts the server's message once, not the default one", async () => {
  fake.mutate.mockResolvedValueOnce({ ok: true, message: "Track created.", id: "prj_7" });
  expect(await press({ linkTo: "example.Project", defaultMessage: "Track ready." }))
    .toEqual(["navigate:/example/projects/prj_7", "refresh", "afterSuccess", "success:Track created.|"]);
});

test("a refused verb: one toast under the verb's label with the server's reason; nothing refreshes and nothing is followed", async () => {
  fake.mutate.mockResolvedValueOnce({
    ok: false, message: "Creating the track failed.", id: "prj_7",
    validationErrors: { __all__: ["You are not allowed to modify this proposal."] },
  });
  expect(await press()).toEqual(["danger:Create track|You are not allowed to modify this proposal."]);
});

test("an answer with no outcome: one toast under the verb's label with the shared no-result text; nothing refreshes", async () => {
  fake.mutate.mockResolvedValueOnce(undefined);
  expect(await press()).toEqual(["danger:Create track|The action returned no result."]);
});

test("a transport failure: one toast from the bar under the verb's label with the error's message", async () => {
  // The data layer's own error notification is outside this harness: the transport is faked.
  fake.mutate.mockRejectedValueOnce(new Error("Request failed."));
  expect(await press()).toEqual(["danger:Create track|Request failed."]);
});

test("an empty success message with a default message: the bar toasts the default", async () => {
  fake.mutate.mockResolvedValueOnce({ ok: true, message: "" });
  expect(await press({ defaultMessage: "Track ready." })).toEqual(["refresh", "afterSuccess", "success:Track ready.|"]);
});
