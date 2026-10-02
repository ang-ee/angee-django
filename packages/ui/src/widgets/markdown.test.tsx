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

  test("shows editor toolbar only while the prose control has focus", () => {
    const Editor = markdownEditorWidget.edit;
    render(<Editor value="" field={{ label: "Body" }} />);

    expect(screen.queryByRole("button", { name: "Bold" })).toBeNull();
    fireEvent.focus(screen.getByLabelText("Body"));
    expect(screen.getByRole("button", { name: "Bold" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Rendered preview" })).toBeTruthy();
    expect(screen.getByLabelText("Body")).toBeTruthy();
    fireEvent.blur(screen.getByLabelText("Body"), { relatedTarget: document.body });
    expect(screen.queryByRole("button", { name: "Bold" })).toBeNull();
  });

  test("renders prose as text when read only", () => {
    const Editor = markdownEditorWidget.edit;
    render(<Editor value="**Saved prose**" readOnly />);
    expect(screen.getByText("Saved prose").tagName).toBe("STRONG");
    expect(screen.queryByRole("button", { name: "Bold" })).toBeNull();
  });

  test("opens saved prose as rendered text with a reachable source view", () => {
    const Editor = markdownEditorWidget.edit;
    render(<Editor value="**Saved prose**" field={{ label: "Body" }} />);
    // The hidden source editor also holds the words; the rendered preview is the STRONG.
    const rendered = () => screen.queryAllByText("Saved prose").filter((node) => node.tagName === "STRONG");
    expect(rendered()).toHaveLength(1);
    const sourceButton = screen.getByRole("button", { name: "Markdown source" });
    const preview = rendered()[0]!.closest("[tabindex]");
    expect(preview).not.toBeNull();
    fireEvent.focus(preview!);
    expect(screen.getByRole("button", { name: "Bold" })).toBeTruthy();
    fireEvent.click(sourceButton);
    expect(rendered()).toHaveLength(0);
    expect(screen.getByLabelText("Body").className).not.toContain("hidden");
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

    fireEvent.focus(screen.getByLabelText("Body"));
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
