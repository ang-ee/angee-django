// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { FormProvider, useForm } from "react-hook-form";
import { afterEach, expect, test, vi } from "vitest";

import { AppRuntimeProvider } from "../../runtime";
import { createUiTestProviders } from "../../testing";
import { defaultWidgets } from "../../widgets";
import { DescriptorFieldList } from "./DescriptorFieldList";
import { deserializeFormSpec, formSpecInitialValues } from "./form-spec";

const { Provider, clearClients } = createUiTestProviders();
afterEach(() => { cleanup(); clearClients(); });

function fixture(widget: "select" | "combobox") {
  const fields = deserializeFormSpec({ properties: {
    count: { type: "integer", title: "Count", default: 0, options: [
      { value: 0, label: "Zero" }, { value: 2, label: "Two" },
    ] },
    choice: { title: "Choice", widget, default: 1, options: [
      { value: 1, label: "Numeric one" }, { value: "1", label: "Text one" },
    ] },
    flag: { type: "boolean", title: "Flag", default: false, options: [
      { value: false, label: "No" }, { value: true, label: "Yes" },
    ] },
    frozen: { type: "integer", title: "Frozen", default: 7, readOnly: true,
      options: [{ value: 7, label: "Seven" }] },
  } }, defaultWidgets);
  const submit = vi.fn();
  function Harness() {
    const form = useForm({ defaultValues: formSpecInitialValues(fields, {}) });
    return <FormProvider {...form}><form onSubmit={form.handleSubmit(submit)}>
      <DescriptorFieldList fields={fields} />
      <button type="submit">Save</button>
    </form></FormProvider>;
  }
  render(<Provider><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}><Harness /></AppRuntimeProvider></Provider>);
  return submit;
}

async function choose(label: string, option: string) {
  fireEvent.click(screen.getByRole("combobox", { name: label }));
  const item = await screen.findByRole("option", { name: option });
  fireEvent.pointerDown(item, { pointerType: "mouse" });
  fireEvent.click(item);
}

test.each(["select", "combobox"] as const)("%s preserves numeric and boolean values with readable and read-only labels", async (widget) => {
  const submit = fixture(widget);
  expect(screen.getByRole("combobox", { name: "Count" }).textContent).toContain("Zero");
  expect(screen.getByRole("combobox", { name: "Choice" }).textContent).toContain("Numeric one");
  expect(screen.getByRole("combobox", { name: "Flag" }).textContent).toContain("No");
  expect(screen.getByText("Seven")).toBeTruthy();
  expect(screen.queryByRole("combobox", { name: "Frozen" })).toBeNull();

  await choose("Count", "Two");
  await choose("Choice", "Text one");
  await choose("Flag", "Yes");
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(submit.mock.calls.at(-1)?.[0]).toEqual({ count: 2, choice: "1", flag: true, frozen: 7 }));

  await choose("Count", "Zero");
  await choose("Choice", "Numeric one");
  await choose("Flag", "No");
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(submit.mock.calls.at(-1)?.[0]).toEqual({ count: 0, choice: 1, flag: false, frozen: 7 }));
});
