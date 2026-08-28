// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import type { AgentRosterItem } from "../documents";
import { AgentChooser } from "./AgentChooser";

afterEach(cleanup);

function agent(id: string, name: string, model: string | null): AgentRosterItem {
  return {
    id,
    name,
    runtime_status: "RUNNING",
    is_template: false,
    updated_at: "2026-06-26T00:00:00Z",
    model: model === null ? null : { name: model },
  } as AgentRosterItem;
}

const AGENTS: readonly AgentRosterItem[] = [
  agent("a1", "Scout", "claude-opus"),
  agent("a2", "Ranger", "claude-haiku"),
];

describe("AgentChooser", () => {
  test("the trigger labels the switcher and shows the selected agent + model handle", () => {
    render(
      <AgentChooser
        agents={AGENTS}
        value="a1"
        onSelect={vi.fn()}
        status="ready"
        statusLabel="Ready"
      />,
    );
    const trigger = screen.getByRole("combobox", { name: "Switch agent" });
    expect(trigger.textContent).toContain("Scout");
    expect(trigger.textContent).toContain("claude-opus");
    // The connection-status dot carries a text alternative (never color-only).
    expect(screen.getByRole("img", { name: "Ready" })).toBeDefined();
  });

  test("falls back to fallbackName/handle when the value is not in the list", () => {
    render(
      <AgentChooser
        agents={AGENTS}
        value="unknown"
        onSelect={vi.fn()}
        status="connecting"
        statusLabel="Connecting…"
        fallbackName="Default agent"
        fallbackHandle="claude-sonnet"
      />,
    );
    const trigger = screen.getByRole("combobox", { name: "Switch agent" });
    expect(trigger.textContent).toContain("Default agent");
    expect(trigger.textContent).toContain("claude-sonnet");
  });

  test("opening lists every running agent, marks the active one, and selecting calls onSelect", () => {
    const onSelect = vi.fn();
    render(
      <AgentChooser
        agents={AGENTS}
        value="a1"
        onSelect={onSelect}
        status="ready"
        statusLabel="Ready"
      />,
    );
    fireEvent.click(screen.getByRole("combobox", { name: "Switch agent" }));
    const options = screen.getAllByRole("option");
    expect(options).toHaveLength(2);
    const active = options.find((o) => o.textContent?.includes("Scout"));
    expect(active?.getAttribute("aria-selected")).toBe("true");
    // Each row dot has a runtime-status text alternative.
    expect(screen.getAllByRole("img", { name: "Running" }).length).toBe(2);
    const other = options.find((o) => o.textContent?.includes("Ranger"));
    fireEvent.pointerDown(other!);
    fireEvent.pointerUp(other!);
    fireEvent.click(other!);
    expect(onSelect).toHaveBeenCalledWith("a2");
  });
});
