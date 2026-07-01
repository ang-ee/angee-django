// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test } from "vitest";

import { ChatBar } from "./index";

describe("ChatBar", () => {
  afterEach(() => {
    cleanup();
  });

  test("renders the start and end slots in one dense header row", () => {
    const { container } = render(
      <ChatBar
        start={<span>chooser</span>}
        end={<button type="button">options</button>}
      />,
    );

    // A single <header> frame carrying both slots. It is deliberately NOT a `banner`
    // landmark: in the app it nests inside the chat surface, so it exposes no page-level
    // role of its own — only the dense bar layout with its start/end slots.
    const header = container.querySelector("header");
    expect(header).toBeTruthy();
    expect(screen.getByText("chooser")).toBeTruthy();
    expect(screen.getByRole("button", { name: "options" })).toBeTruthy();
  });

  test("omits the trailing slot wrapper when end is absent", () => {
    render(<ChatBar start={<span>only start</span>} />);

    expect(screen.getByText("only start")).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
  });
});
