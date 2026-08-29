// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test } from "vitest";

import { MessageDaySeparator, MessageFeed, MessageRow } from "./index";

describe("MessageFeed", () => {
  afterEach(() => {
    cleanup();
  });

  test("renders a labelled single-column list of rows", () => {
    render(
      <MessageFeed label="Comments">
        <MessageRow author="Ada">First</MessageRow>
        <MessageRow author="Sam">Second</MessageRow>
      </MessageFeed>,
    );

    const list = screen.getByRole("list", { name: "Comments" });
    expect(list.tagName).toBe("UL");
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
  });

  test("marks the feed busy while messages load", () => {
    render(
      <MessageFeed label="Comments" busy>
        <MessageRow author="Ada">…</MessageRow>
      </MessageFeed>,
    );

    expect(screen.getByRole("list", { name: "Comments" }).getAttribute("aria-busy")).toBe("true");
  });

  test("renders a day separator between day groups", () => {
    render(
      <MessageFeed label="Comments">
        <MessageDaySeparator>Today</MessageDaySeparator>
        <MessageRow author="Ada">Hi</MessageRow>
      </MessageFeed>,
    );

    expect(screen.getByText("Today")).toBeTruthy();
    // The separator and the row are both list items in the one column.
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
  });
});
