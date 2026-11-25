// @vitest-environment happy-dom

import * as React from "react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import { AppRuntimeProvider } from "../../runtime";
import { defaultWidgets } from "../../widgets";
import type { FieldDescriptor } from "../page";
import { LabeledDescriptorField } from "./DescriptorFieldList";

afterEach(cleanup);

/** A labelled field holding `initial`, reporting every value it is given. */
function renderField(field: FieldDescriptor, initial: unknown) {
  const changes = vi.fn();
  function Harness() {
    const [value, setValue] = React.useState<unknown>(initial);
    return <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
      <LabeledDescriptorField field={field} value={value} onChange={(next) => { changes(next); setValue(next); }} />
    </AppRuntimeProvider>;
  }
  render(<Harness />);
  return changes;
}

describe("a field whose empty value is null has no present-but-empty state", () => {
  test("a nullable date holding null shows its own control with the Left empty hint, no Set value, and keeps a picked day", async () => {
    const changes = renderField({ name: "due", label: "Due", widget: "date", nullable: true }, null);
    expect(screen.queryByRole("button", { name: "Set value" })).toBeNull();
    expect(screen.getByText("Left empty")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /^Due/ }));
    const day = within(await screen.findByRole("grid")).getAllByRole("button").find((button) => button.textContent?.trim() === "15")!;
    fireEvent.click(day);
    const picked = changes.mock.calls.at(-1)![0];
    expect(typeof picked === "string" && picked.endsWith("-15")).toBe(true);
    expect(screen.queryByText("Left empty")).toBeNull();
  });

  test("a nullable number holding nothing, with no default, shows its own control and never writes a 0", () => {
    const changes = renderField({ name: "limit", label: "Limit", kind: "integer", widget: "integer", nullable: true }, undefined);
    expect(screen.queryByRole("button", { name: "Set value" })).toBeNull();
    expect(screen.getByText("Not set")).toBeTruthy();
    const input = screen.getByRole("textbox", { name: "Limit" }) as HTMLInputElement;
    expect(input.value).toBe("");
    fireEvent.change(input, { target: { value: "7" } });
    expect(input.value).toBe("7");
    expect(changes.mock.calls.map(([value]) => value)).not.toContain(0);
  });

  test("Set value never writes a value the field already holds: an untyped value holding null shows its own control instead", async () => {
    const changes = renderField({ name: "payload", label: "Payload", kind: "any", widget: "json", nullable: true }, null);
    expect(screen.queryByRole("button", { name: "Set value" })).toBeNull();
    expect(screen.getByText("Left empty")).toBeTruthy();
    // The editor loads on its own time; once it is there, it has mounted without writing anything.
    await waitFor(() => expect(document.querySelector(".cm-content")).not.toBeNull());
    expect(changes).not.toHaveBeenCalled();
  });

  test("a nullable integer choice holding null keeps Set value, which writes its empty choice, never a 0", () => {
    const changes = renderField({ name: "level", label: "Level", kind: "integer", widget: "select",
      options: [{ value: "1", label: "One" }], nullable: true }, null);
    fireEvent.click(screen.getByRole("button", { name: "Set value" }));
    expect(changes).toHaveBeenLastCalledWith("");
  });

  test("guard: a nullable text holding null keeps Set value, which writes its empty text", () => {
    const changes = renderField({ name: "note", label: "Note", kind: "string", nullable: true }, null);
    fireEvent.click(screen.getByRole("button", { name: "Set value" }));
    expect(changes).toHaveBeenLastCalledWith("");
  });

  test("guard: an omittable number with a default, holding nothing, offers Use default, which writes the default", () => {
    const changes = renderField({ name: "limit", label: "Limit", kind: "integer", widget: "integer", omittable: true, hasDefault: true, defaultValue: 60 }, undefined);
    expect(screen.queryByRole("button", { name: "Set value" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Use default" }));
    expect(changes).toHaveBeenLastCalledWith(60);
  });
});
