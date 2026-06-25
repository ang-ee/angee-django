// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import { Button } from "./button";
import { InlineTextAction } from "./inline-text-action";

describe("InlineTextAction", () => {
  afterEach(() => {
    cleanup();
  });

  test("opens from the trigger and focuses the input", () => {
    render(
      <InlineTextAction
        inputLabel="Folder name"
        submitLabel="Create"
        onSubmit={vi.fn()}
        renderTrigger={({ open }) => (
          <Button type="button" onClick={open}>
            New folder
          </Button>
        )}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "New folder" }));

    const input = screen.getByRole("textbox", { name: "Folder name" });
    expect(document.activeElement).toBe(input);
  });

  test("submits a trimmed non-empty value", () => {
    const onSubmit = vi.fn();
    render(
      <InlineTextAction
        defaultOpen
        inputLabel="Folder name"
        submitLabel="Create"
        onSubmit={onSubmit}
      />,
    );

    fireEvent.change(screen.getByRole("textbox", { name: "Folder name" }), {
      target: { value: "  Ideas  " },
    });
    fireEvent.submit(screen.getByRole("form", { name: "Folder name" }));

    expect(onSubmit).toHaveBeenCalledWith("Ideas");
    expect(screen.queryByRole("textbox", { name: "Folder name" })).toBeNull();
  });

  test("Escape cancels and restores the trigger", () => {
    const onSubmit = vi.fn();
    const onCancel = vi.fn();
    render(
      <InlineTextAction
        inputLabel="Name"
        onSubmit={onSubmit}
        onCancel={onCancel}
        renderTrigger={({ open }) => (
          <Button type="button" onClick={open}>
            Rename
          </Button>
        )}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Rename" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
      target: { value: "Draft" },
    });
    fireEvent.keyDown(screen.getByRole("textbox", { name: "Name" }), {
      key: "Escape",
    });

    expect(onSubmit).not.toHaveBeenCalled();
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Rename" })).toBeTruthy();
  });

  test("blur outside the form cancels without submitting", () => {
    const onSubmit = vi.fn();
    const onCancel = vi.fn();
    render(
      <>
        <InlineTextAction
          defaultOpen
          inputLabel="Title"
          onSubmit={onSubmit}
          onCancel={onCancel}
        />
        <button type="button">Outside</button>
      </>,
    );

    fireEvent.blur(screen.getByRole("textbox", { name: "Title" }), {
      relatedTarget: screen.getByRole("button", { name: "Outside" }),
    });

    expect(onSubmit).not.toHaveBeenCalled();
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("textbox", { name: "Title" })).toBeNull();
  });

  test("busy state disables the input and submit action", () => {
    render(
      <InlineTextAction
        defaultOpen
        busy
        value="Existing"
        inputLabel="Name"
        onSubmit={vi.fn()}
      />,
    );

    expect(
      (screen.getByRole("textbox", { name: "Name" }) as HTMLInputElement)
        .disabled,
    ).toBe(true);
    expect(
      (screen.getByRole("button", { name: "Save" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    expect(screen.getByRole("form", { name: "Name" }).getAttribute("aria-busy"))
      .toBe("true");
  });

  test("unchanged rename values cancel instead of submitting", () => {
    const onSubmit = vi.fn();
    const onCancel = vi.fn();
    render(
      <InlineTextAction
        defaultOpen
        value="Existing"
        inputLabel="Name"
        onSubmit={onSubmit}
        onCancel={onCancel}
      />,
    );

    fireEvent.submit(screen.getByRole("form", { name: "Name" }));

    expect(onSubmit).not.toHaveBeenCalled();
    expect(onCancel).toHaveBeenCalledTimes(1);
  });
});
