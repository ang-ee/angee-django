// @vitest-environment happy-dom

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import { markdownEditorWidget, markdownPreviewWidget } from "./markdown";

describe("markdown widgets", () => {
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
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

  test("defers editor change notifications outside CodeMirror transactions", () => {
    vi.useFakeTimers();
    const onChange = vi.fn();
    const Editor = markdownEditorWidget.edit;
    render(<Editor value="" field={{ label: "Body" }} onChange={onChange} />);

    fireEvent.click(screen.getByRole("button", { name: "Bold" }));

    expect(onChange).not.toHaveBeenCalled();
    act(() => {
      vi.advanceTimersByTime(16);
    });
    expect(onChange).toHaveBeenCalledWith("**bold text**");
  });
});
