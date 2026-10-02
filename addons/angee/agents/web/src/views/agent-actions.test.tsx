// @vitest-environment happy-dom

import * as React from "react";
import { cleanup, fireEvent, render, renderHook, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
import type { Row } from "@angee/metadata";
import { ModalsHost, RecordActionBar, ToastProvider, parsePageActions } from "@angee/ui";
import { createUiTestProviders } from "@angee/ui/testing";

import { AGENT_MODEL, useAgentLifecycleActions } from "./agent-actions";

// Keep the real action bar, confirmation dialogs and parsers; only the verb transport
// is replaced, so each test sees exactly which verb ran with which options.
const mocks = vi.hoisted(() => ({
  runs: new Map<string, ReturnType<typeof vi.fn>>(),
  options: new Map<string, unknown>(),
}));

vi.mock("@angee/ui", async () => {
  const actual = await vi.importActual<typeof import("@angee/ui")>("@angee/ui");
  return {
    ...actual,
    useRecordActionMutation: (field: string, options: unknown) => {
      const run = mocks.runs.get(field) ?? vi.fn(async () => undefined);
      mocks.runs.set(field, run);
      mocks.options.set(field, options);
      return [run, { fetching: false, error: null }];
    },
  };
});

const { Provider } = createUiTestProviders({
  queryClientConfig: { defaultOptions: { mutations: { retry: false }, queries: { retry: false } } },
});

afterEach(() => {
  cleanup();
  mocks.runs.clear();
  mocks.options.clear();
});

const VERBS = ["provision_agent", "adopt_agent", "replace_agent", "reprovision_agent", "deprovision_agent"];

const draft: Row = { id: "agt_1", can_provision: true, can_deprovision: false };
const conflicted = (kind: "WORKSPACE" | "SERVICE"): Row => ({
  id: "agt_1",
  can_provision: false,
  can_adopt: true,
  can_replace: true,
  can_reprovision: false,
  can_deprovision: true,
  conflict_kind: kind,
  conflict_name: kind === "WORKSPACE" ? "ws-taken" : "agent-ws-taken",
});

function AgentToolbar({ record }: { record: Row }): React.ReactElement {
  const actions = parsePageActions(useAgentLifecycleActions());
  return <RecordActionBar record={record} actions={actions} reload={vi.fn()} />;
}

function renderToolbar(record: Row): void {
  render(
    <Provider>
      <ModalsHost>
        <ToastProvider>
          <AgentToolbar record={record} />
        </ToastProvider>
      </ModalsHost>
    </Provider>,
  );
}

describe("agent lifecycle toolbar", () => {
  test("offers Provision until a conflicting instance is recorded, then Adopt and Replace", () => {
    renderToolbar(draft);
    expect(screen.getByRole("button", { name: "Provision" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Adopt existing" })).toBeNull();
    cleanup();

    renderToolbar(conflicted("WORKSPACE"));
    expect(screen.queryByRole("button", { name: "Provision" })).toBeNull();
    expect(screen.getByRole("button", { name: "Adopt existing" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Replace existing" })).toBeTruthy();
  });

  test("Adopt says the container is kept as it is, and runs only once confirmed", async () => {
    renderToolbar(conflicted("WORKSPACE"));

    fireEvent.click(screen.getByRole("button", { name: "Adopt existing" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Adopt the existing workspace?" });
    expect(dialog.textContent).toContain("operator workspace “ws-taken”");
    expect(dialog.textContent).toContain("kept as it is, with the configuration and credentials it was created with");
    expect(dialog.textContent).toContain("Reprovision afterwards");
    expect(mocks.runs.get("adopt_agent")).not.toHaveBeenCalled();

    fireEvent.click(within(dialog).getByRole("button", { name: "Adopt existing" }));
    await waitFor(() => expect(mocks.runs.get("adopt_agent")).toHaveBeenCalledOnce());
  });

  test("words Replace and Deprovision for the conflicting instance's kind", async () => {
    renderToolbar(conflicted("SERVICE"));

    fireEvent.click(screen.getByRole("button", { name: "Replace existing" }));
    const replace = await screen.findByRole("alertdialog", { name: "Replace the existing service?" });
    expect(replace.textContent).toContain("operator service “agent-ws-taken” and this agent's workspace");
    fireEvent.click(within(replace).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());

    fireEvent.click(screen.getByRole("button", { name: "Actions" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "Deprovision" }));
    const deprovision = await screen.findByRole("alertdialog", { name: "Deprovision agent?" });
    expect(deprovision.textContent).toContain("operator service “agent-ws-taken” with it if the service mounts");
  });

  test("every lifecycle verb refreshes the agent after a failure, which it records", () => {
    renderHook(() => useAgentLifecycleActions());

    expect([...mocks.options.keys()].sort()).toEqual([...VERBS].sort());
    for (const verb of VERBS) {
      expect(mocks.options.get(verb)).toMatchObject({ invalidateModels: [AGENT_MODEL], invalidateOnFailure: true });
    }
  });
});
