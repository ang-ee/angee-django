// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import { colorWidget } from "./color";

const Read = colorWidget.read;
const Edit = colorWidget.edit;

describe("color widget", () => {
  afterEach(() => {
    cleanup();
  });

  test("reads a hex colour as a tinted swatch beside the value", () => {
    const { container } = render(<Read value="#17343d" />);
    const swatch = container.querySelector('[role="img"]') as HTMLElement;
    expect(swatch.style.backgroundColor).not.toBe("");
    expect(swatch.getAttribute("aria-label")).toBe("#17343d");
    expect(screen.getByText("#17343d")).toBeTruthy();
  });

  test("reads an empty colour as a dashed swatch and a dash", () => {
    const { container } = render(<Read value="" />);
    const swatch = container.querySelector('[role="img"]') as HTMLElement;
    expect(swatch.className).toContain("border-dashed");
    expect(swatch.style.backgroundColor).toBe("");
    expect(screen.getByText("—")).toBeTruthy();
  });

  test("edits through the native picker and the hex text, both writing #rrggbb", () => {
    const onChange = vi.fn();
    render(<Edit value="#17343d" onChange={onChange} field={{ label: "Colour" }} />);
    fireEvent.change(screen.getByLabelText("Colour picker"), { target: { value: "#ff0000" } });
    expect(onChange).toHaveBeenLastCalledWith("#ff0000");
    fireEvent.change(screen.getByLabelText("Colour"), { target: { value: "#00ff00" } });
    expect(onChange).toHaveBeenLastCalledWith("#00ff00");
  });

  test("a read-only colour disables the picker and the text", () => {
    render(<Edit value="#17343d" readOnly field={{ label: "Colour" }} />);
    expect((screen.getByLabelText("Colour picker") as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByLabelText("Colour") as HTMLInputElement).readOnly).toBe(true);
  });
});
