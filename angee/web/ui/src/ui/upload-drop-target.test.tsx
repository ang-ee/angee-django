// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import { UploadDropTarget } from "./upload-drop-target";

function fileTransfer(files: readonly File[]): DataTransfer {
  return {
    types: ["Files"],
    files,
    dropEffect: "none",
  } as unknown as DataTransfer;
}

describe("UploadDropTarget", () => {
  afterEach(() => {
    cleanup();
  });

  test("shows the overlay while files hover and emits dropped files", () => {
    const onFiles = vi.fn();
    const file = new File(["hello"], "hello.txt", { type: "text/plain" });
    render(
      <UploadDropTarget onFiles={onFiles} overlay="Drop files">
        <span>Body</span>
      </UploadDropTarget>,
    );

    const target = screen.getByText("Body").parentElement!;
    fireEvent.dragEnter(target, { dataTransfer: fileTransfer([file]) });

    expect(screen.getByText("Drop files")).toBeTruthy();

    fireEvent.drop(target, { dataTransfer: fileTransfer([file]) });

    expect(onFiles).toHaveBeenCalledWith([file]);
    expect(screen.queryByText("Drop files")).toBeNull();
  });

  test("ignores drops while disabled", () => {
    const onFiles = vi.fn();
    const file = new File(["hello"], "hello.txt");
    render(
      <UploadDropTarget disabled onFiles={onFiles} overlay="Drop files">
        <button type="button">Body</button>
      </UploadDropTarget>,
    );

    const target = screen.getByRole("button", { name: "Body" }).parentElement!;
    fireEvent.dragEnter(target, { dataTransfer: fileTransfer([file]) });
    fireEvent.drop(target, { dataTransfer: fileTransfer([file]) });

    expect(target.getAttribute("aria-disabled")).toBeNull();
    expect(target.hasAttribute("data-file-drop-disabled")).toBe(true);
    expect(screen.getByRole("button", { name: "Body" }).hasAttribute("disabled")).toBe(false);
    expect(onFiles).not.toHaveBeenCalled();
    expect(screen.queryByText("Drop files")).toBeNull();
  });

  test("prevents the browser's default file drop when disabled", () => {
    const onFiles = vi.fn();
    const file = new File(["hello"], "hello.txt");
    render(
      <UploadDropTarget disabled onFiles={onFiles} overlay="Drop files">
        <span>Body</span>
      </UploadDropTarget>,
    );

    const target = screen.getByText("Body").parentElement!;
    const drop = fireEvent.drop(target, { dataTransfer: fileTransfer([file]) });

    expect(drop).toBe(false);
    expect(onFiles).not.toHaveBeenCalled();
  });
});
