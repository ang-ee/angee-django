// @vitest-environment happy-dom

import * as React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";

import { floatWidget, integerWidget } from "./number";

afterEach(cleanup);

test("number list cells show an em dash for missing values while preserving zero", () => {
  const IntegerCell = integerWidget.cell!;
  const FloatCell = floatWidget.cell!;
  render(<><IntegerCell value={null} /><FloatCell value={0} /></>);
  expect(screen.getByText("—")).toBeTruthy();
  expect(screen.getByText("0")).toBeTruthy();
});

test("number reads preserve ungrouped integer, decimal, and negative spellings", () => {
  const IntegerRead = integerWidget.read;
  const FloatRead = floatWidget.read;
  render(<><IntegerRead value={1234} /><FloatRead value={1.5} /><FloatRead value={-2.5} /></>);
  expect(screen.getByText("1234")).toBeTruthy();
  expect(screen.getByText("1.5")).toBeTruthy();
  expect(screen.getByText("-2.5")).toBeTruthy();
});

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
