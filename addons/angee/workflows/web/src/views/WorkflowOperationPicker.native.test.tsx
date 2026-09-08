// @vitest-environment happy-dom

import * as React from "react";
import { AppRuntimeProvider, defaultWidgets } from "@angee/ui";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import {
  WorkflowOperationPicker,
  type WorkflowOperationChoice,
} from "./WorkflowOperationPicker";

const operations: readonly WorkflowOperationChoice[] = [
  {
    key: "call",
    label: "Run callable",
    category: "Activity",
    description: "Run the configured callable.",
    selectable: true,
    effect: "EXTERNAL",
    effect_description: "Calls the configured service.",
  },
  {
    key: "gate",
    label: "Wait for approval",
    category: "Control",
    description: "Pause until an assignee decides.",
    selectable: true,
    effect: "NONE",
    effect_description: "",
  },
  {
    key: "handler",
    label: "Handler",
    category: "Internal",
    description: "Abstract runtime handler.",
    selectable: false,
    effect: "UNKNOWN",
    effect_description: "",
  },
];

afterEach(cleanup);

function renderPicker(props: Partial<React.ComponentProps<typeof WorkflowOperationPicker>> = {}) {
  const onChoose = vi.fn();
  const onOpenChange = vi.fn();
  render(
    <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
      <WorkflowOperationPicker
        operations={operations}
        open
        onChoose={onChoose}
        onOpenChange={onOpenChange}
        {...props}
      />
    </AppRuntimeProvider>,
  );
  return { onChoose, onOpenChange };
}

test("groups and searches selectable declared operation metadata", async () => {
  renderPicker();

  expect(screen.getByRole("heading", { name: "Add a step" })).toBeTruthy();
  expect(screen.getByText("Activity")).toBeTruthy();
  expect(screen.getByText("Control")).toBeTruthy();
  expect(screen.getByText("Effect: Calls the configured service.")).toBeTruthy();
  expect(screen.getByText("Effect: no subject effect")).toBeTruthy();
  expect(screen.queryByText("Handler")).toBeNull();

  fireEvent.change(screen.getByPlaceholderText("Search operations…"), {
    target: { value: "approval" },
  });
  await waitFor(() => expect(screen.queryByText("Run callable")).toBeNull());
  expect(screen.getByText("Wait for approval")).toBeTruthy();
});

test("supports keyboard choice, Escape, and focus return", async () => {
  const onChoose = vi.fn();
  function Fixture(): React.ReactElement {
    const [open, setOpen] = React.useState(false);
    const triggerRef = React.useRef<HTMLButtonElement>(null);
    return (
      <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
        <button ref={triggerRef} type="button" onClick={() => setOpen(true)}>Add step</button>
        <WorkflowOperationPicker
          operations={operations}
          open={open}
          onOpenChange={setOpen}
          onChoose={onChoose}
          finalFocus={triggerRef}
        />
      </AppRuntimeProvider>
    );
  }
  render(<Fixture />);
  const trigger = screen.getByRole("button", { name: "Add step" });
  trigger.focus();
  fireEvent.click(trigger);
  const search = await screen.findByPlaceholderText("Search operations…");
  await waitFor(() => expect(document.activeElement).toBe(search));
  fireEvent.change(search, { target: { value: "approval" } });
  fireEvent.keyDown(search, { key: "ArrowDown" });
  fireEvent.keyDown(search, { key: "Enter" });
  expect(onChoose).toHaveBeenCalledWith("gate");
  await waitFor(() => expect(document.activeElement).toBe(trigger));

  fireEvent.click(trigger);
  fireEvent.keyDown(await screen.findByPlaceholderText("Search operations…"), { key: "Escape" });
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(document.activeElement).toBe(trigger);
});

test("uses the operation key to distinguish otherwise identical choices", async () => {
  const duplicateChoice: Omit<WorkflowOperationChoice, "key"> = {
    label: "Duplicate operation",
    category: "Activity",
    description: "The same declared description.",
    selectable: true,
    effect: "READ",
    effect_description: "Reads the same records.",
  };
  const { onChoose } = renderPicker({
    operations: [
      { key: "duplicate.first", ...duplicateChoice },
      { key: "duplicate.second", ...duplicateChoice },
    ],
  });

  const search = screen.getByPlaceholderText("Search operations…");
  fireEvent.change(search, { target: { value: "duplicate.second" } });
  await waitFor(() => {
    const choices = screen.getAllByRole("option", { name: /Duplicate operation/ });
    expect(choices).toHaveLength(2);
    expect(choices.filter((choice) => choice.getAttribute("aria-selected") === "true"))
      .toHaveLength(1);
  });
  fireEvent.keyDown(search, { key: "Enter" });
  expect(onChoose).toHaveBeenCalledWith("duplicate.second");
});

test("renders loading, error, and empty states without choices", () => {
  const view = renderPicker({ loading: true, operations: [] });
  expect(screen.getByText("Loading operations")).toBeTruthy();

  cleanup();
  renderPicker({ error: "Operation metadata failed.", operations: [] });
  expect(screen.getByText("Operation metadata failed.")).toBeTruthy();

  cleanup();
  renderPicker({ operations: [] });
  expect(screen.getByText("No matching operations.")).toBeTruthy();
  expect(view.onChoose).not.toHaveBeenCalled();
});
