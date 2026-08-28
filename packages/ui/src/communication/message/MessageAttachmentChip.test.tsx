// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import { MessageAttachmentChip } from "./index";

describe("MessageAttachmentChip", () => {
  afterEach(() => {
    cleanup();
  });

  test("renders the label and the remove slot", () => {
    render(
      <MessageAttachmentChip remove={<button type="button">remove</button>}>photo.png</MessageAttachmentChip>,
    );

    expect(screen.getByText("photo.png")).toBeTruthy();
    expect(screen.getByRole("button", { name: "remove" })).toBeTruthy();
  });

  test("makes the label a button that fires onClick", () => {
    const onClick = vi.fn();
    render(<MessageAttachmentChip onClick={onClick}>Current view</MessageAttachmentChip>);

    fireEvent.click(screen.getByRole("button", { name: "Current view" }));

    expect(onClick).toHaveBeenCalledTimes(1);
  });

  test("renders a plain (non-button) label when onClick is omitted", () => {
    render(<MessageAttachmentChip>plain.txt</MessageAttachmentChip>);

    expect(screen.getByText("plain.txt")).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
  });
});
