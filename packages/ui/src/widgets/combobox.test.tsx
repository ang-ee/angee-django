// @vitest-environment happy-dom
import * as React from "react";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, expect, test } from "vitest";

import { comboboxWidget } from "./combobox";

afterEach(cleanup);

test("retains a selected option from a non-option initial value", async () => {
  const Edit = comboboxWidget.edit!;
  const changes: unknown[] = [];
  const options = Array.from({ length: 160 }, (_, index) => ({
    value: `C${String(index).padStart(3, "0")}`,
    label: `Currency ${index}`,
  }));
  options[145] = { value: "USD", label: "USD — US Dollar" };
  function Harness() {
    const [value, setValue] = React.useState<string>("$");
    return (
      <Edit
        value={value}
        onChange={(next) => {
          changes.push(next);
          setValue(next);
        }}
        field={{
          name: "currency",
          label: "Invoice currency",
          options,
        }}
      />
    );
  }
  render(<Harness />);
  const combo = screen.getByRole("combobox", { name: "Invoice currency" });
  expect(combo.textContent).toContain("Invoice currency");
  expect(changes).toEqual([]);
  fireEvent.click(combo);
  expect(changes).toEqual([]);
  fireEvent.change(screen.getByRole("combobox", { name: "Search options" }), {
    target: { value: "USD" },
  });
  expect(changes).toEqual([]);
  const usd = screen.getByRole("option", { name: "USD — US Dollar" });
  fireEvent.pointerDown(usd, { pointerType: "mouse" });
  fireEvent.click(usd);
  expect(changes).toEqual(["USD"]);
  await waitFor(() =>
    expect(combo.textContent).toContain("USD — US Dollar"),
  );
});
