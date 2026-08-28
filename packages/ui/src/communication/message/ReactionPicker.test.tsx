// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import { ReactionPicker } from "./index";

describe("ReactionPicker", () => {
  afterEach(() => {
    cleanup();
  });

  test("renders one count-less pill per option using the shared accessible-name convention", () => {
    render(<ReactionPicker options={["👍", "🎉"]} onToggle={() => undefined} />);

    // The count-less variant of `ReactionBar`'s "👍 reaction, 3" convention.
    expect(screen.getByRole("button", { name: "👍 reaction" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "🎉 reaction" })).toBeTruthy();
  });

  test("marks the reactions the current user already applied as pressed", () => {
    render(<ReactionPicker options={["👍", "🎉"]} active={["👍"]} onToggle={() => undefined} />);

    expect(screen.getByRole("button", { name: "👍 reaction" }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByRole("button", { name: "🎉 reaction" }).getAttribute("aria-pressed")).toBe("false");
  });

  test("toggles a reaction on click", () => {
    const onToggle = vi.fn();
    render(<ReactionPicker options={["🚀"]} onToggle={onToggle} />);

    fireEvent.click(screen.getByRole("button", { name: "🚀 reaction" }));

    expect(onToggle).toHaveBeenCalledWith("🚀");
  });

  test("renders inert (disabled) pills when onToggle is omitted", () => {
    render(<ReactionPicker options={["❤️"]} />);

    expect(screen.getByRole("button", { name: "❤️ reaction" }).hasAttribute("disabled")).toBe(true);
  });

  test("renders nothing when there are no options", () => {
    const { container } = render(<ReactionPicker options={[]} onToggle={() => undefined} />);

    expect(container.firstChild).toBeNull();
  });
});
