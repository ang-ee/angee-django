// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, test, vi } from "vitest";

import { markdownEditorWidget, markdownPreviewWidget } from "./markdown";

describe("markdown widgets", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  test("renders markdown preview with gfm content", () => {
    const Preview = markdownPreviewWidget.read;
    render(<Preview value={"# Title\n\n- one\n- two"} />);

    expect(screen.getByRole("heading", { name: "Title" })).toBeTruthy();
    expect(screen.getByText("one")).toBeTruthy();
    expect(screen.getByText("two")).toBeTruthy();
  });

  test("renders editor toolbar controls", () => {
    const Editor = markdownEditorWidget.edit;
    render(<Editor value="Body" field={{ label: "Body" }} />);

    expect(screen.getByRole("button", { name: "Bold" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Rendered preview" })).toBeTruthy();
    expect(screen.getByLabelText("Body")).toBeTruthy();
  });

  test("publishes toolbar edits immediately and accepts controlled value feedback", () => {
    const onChange = vi.fn();
    const errors = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const Editor = markdownEditorWidget.edit;
    function Harness() {
      const [value, setValue] = useState("");
      return <><Editor value={value} field={{ label: "Body" }} onChange={(next) => { onChange(next); setValue(next); }} />
        <button onClick={() => setValue("Reset content")}>Reset</button></>;
    }
    render(<Harness />);

    fireEvent.click(screen.getByRole("button", { name: "Bold" }));

    expect(onChange).toHaveBeenCalledWith("**bold text**");
    expect(screen.getByRole("textbox", { name: "Body" }).textContent).toBe("**bold text**");
    const reset = screen.getByRole("button", { name: "Reset" });
    // fireEvent.click omits the native focus transfer. Keeping CodeMirror
    // focused makes happy-dom's synchronous selectionchange re-enter its update.
    reset.focus();
    fireEvent.click(reset);
    expect(screen.getByRole("textbox", { name: "Body" }).textContent).toBe("Reset content");
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(errors).not.toHaveBeenCalled();
  });
});
