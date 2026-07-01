// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import { ReactionBar } from "./index";

describe("ReactionBar", () => {
  afterEach(() => {
    cleanup();
  });

  test("gives each pill an accessible name of glyph + count", () => {
    render(
      <ReactionBar
        reactions={[
          { reaction: "👍", count: 3, active: true },
          { reaction: "🎉", count: 1 },
        ]}
        onToggle={() => undefined}
      />,
    );

    const liked = screen.getByRole("button", { name: "👍 reaction, 3" });
    expect(liked.getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByRole("button", { name: "🎉 reaction, 1" })).toBeTruthy();
  });

  test("toggles a reaction on click", () => {
    const onToggle = vi.fn();
    render(<ReactionBar reactions={[{ reaction: "🚀", count: 2 }]} onToggle={onToggle} />);

    fireEvent.click(screen.getByRole("button", { name: "🚀 reaction, 2" }));

    expect(onToggle).toHaveBeenCalledWith("🚀");
  });

  test("renders inert (disabled) pills when onToggle is omitted", () => {
    render(<ReactionBar reactions={[{ reaction: "❤️", count: 8 }]} />);

    expect(screen.getByRole("button", { name: "❤️ reaction, 8" }).hasAttribute("disabled")).toBe(true);
  });

  test("renders nothing when there are no reactions", () => {
    const { container } = render(<ReactionBar reactions={[]} onToggle={() => undefined} />);

    expect(container.firstChild).toBeNull();
  });
});
