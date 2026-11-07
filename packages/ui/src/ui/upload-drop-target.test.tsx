// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import { UploadDropTarget } from "./upload-drop-target";
import { AppRuntimeProvider } from "../runtime";

function fileTransfer(files: readonly File[]): DataTransfer {
  return {
    types: ["Files"],
    files,
    dropEffect: "none",
  } as unknown as DataTransfer;
}

describe("UploadDropTarget", () => {
  test("preview retains the surface but prevents file drops", () => {
    const onFiles = vi.fn();
    render(<AppRuntimeProvider runtime={{ auth: { user: null, status: "authenticated", hasRole: () => false,
      viewAs: { viewAs: { userId: "person" }, currentUser: null, realUser: null, viewablePeople: [], enter: vi.fn(), exit: vi.fn() },
    } }}><UploadDropTarget onFiles={onFiles}><span>Body</span></UploadDropTarget></AppRuntimeProvider>);
    const target = screen.getByText("Body").parentElement!;
    expect(target.hasAttribute("data-file-drop-disabled")).toBe(true);
    fireEvent.drop(target, { dataTransfer: fileTransfer([new File(["text"], "file.txt")]) });
    expect(onFiles).not.toHaveBeenCalled();
  });
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
