// @vitest-environment happy-dom

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";

import { defaultWidgets } from "./index";
import { timeWidget } from "./time";

const Edit = timeWidget.edit;
const Read = timeWidget.read;
const Cell = timeWidget.cell;

describe("time widget", () => {
  test("is the registry's time widget", () => {
    expect(defaultWidgets.time).toBe(timeWidget);
  });

  test("edits a GraphQL time of day through the native time control", () => {
    const onChange = vi.fn();
    const onCommit = vi.fn();
    render(<Edit value="09:30:00" onChange={onChange} onCommit={onCommit} field={{ label: "Starts" }} />);
    const input = screen.getByLabelText<HTMLInputElement>("Starts");
    expect(input.type).toBe("time");
    expect(input.value).toBe("09:30");
    fireEvent.change(input, { target: { value: "14:05" } });
    expect(onChange).toHaveBeenLastCalledWith("14:05");
    fireEvent.change(input, { target: { value: "" } });
    expect(onChange).toHaveBeenLastCalledWith(null);
    fireEvent.blur(input);
    expect(onCommit).toHaveBeenCalledOnce();
  });

  test("falls back to its own label and reads when read-only", () => {
    const { unmount } = render(<Edit value={null} />);
    expect(screen.getByLabelText<HTMLInputElement>("Time").value).toBe("");
    unmount();
    render(<Edit value="08:00" readOnly field={{ label: "Starts" }} />);
    expect(screen.queryByLabelText("Starts")).toBeNull();
    expect(screen.getByText("8:00 AM")).toBeTruthy();
  });

  test("record reads and list cells share the locale clock label", () => {
    render(<><Read value="13:45:00" /><Cell value="07:05:00" /><Cell value={null} /></>);
    expect(screen.getByText("1:45 PM").getAttribute("title")).toBe("13:45:00");
    expect(screen.getByText("7:05 AM")).toBeTruthy();
    expect(screen.getByText("—")).toBeTruthy();
  });
});
