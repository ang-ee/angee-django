// @vitest-environment happy-dom

import * as React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";

import { floatWidget } from "./number";

afterEach(cleanup);

test("a cleared decimal stays editable and publishes blank form state", () => {
  const FloatEdit = floatWidget.edit!;
  function Harness() {
    const [value, setValue] = React.useState<number | string | null>(1.5);
    return <>
      <FloatEdit
        value={value}
        field={{ name: "rate", label: "Rate" }}
        onChange={setValue}
      />
      <output>{JSON.stringify(value)}</output>
    </>;
  }

  render(<Harness />);
  const input = screen.getByRole("textbox", { name: "Rate" });
  input.focus();
  fireEvent.input(input, { target: { value: "" } });
  expect(screen.getByText('""')).toBeTruthy();
  expect(document.activeElement).toBe(input);
  fireEvent.change(input, { target: { value: "2.75" } });
  expect(screen.getByText("2.75")).toBeTruthy();
});
