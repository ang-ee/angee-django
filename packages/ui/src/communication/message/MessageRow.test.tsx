// @vitest-environment happy-dom

import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import { MessageActions, MessageRow } from "./index";

describe("MessageRow", () => {
  test("renders the author, body, and every composed slot", () => {
    render(
      <ul>
        <MessageRow
          author="Ada Lovelace"
          timestamp="2026-07-01T10:00:00Z"
          channel={<span>Email · Inbound</span>}
          attachments={
            <a href="#" download>
              contract.pdf
            </a>
          }
          reactions={<span>👍 1</span>}
          actions={
            <button type="button">Reply</button>
          }
        >
          Shipping today.
        </MessageRow>
      </ul>,
    );

    expect(screen.getByRole("listitem")).toBeTruthy();
    expect(screen.getByText("Ada Lovelace")).toBeTruthy();
    expect(screen.getByText("Shipping today.")).toBeTruthy();
    expect(screen.getByText("Email · Inbound")).toBeTruthy();
    expect(screen.getByRole("link", { name: "contract.pdf" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Reply" })).toBeTruthy();
  });

  test("omits the header row when it carries no author, channel, timestamp, or meta", () => {
    render(
      <ul>
        <MessageRow>Body only</MessageRow>
      </ul>,
    );

    expect(screen.getByText("Body only")).toBeTruthy();
    expect(screen.queryByText("undefined")).toBeNull();
  });

  test("its actions reveal on hover or focus, and always show where the primary pointer is coarse, which never hovers", () => {
    render(
      <ul>
        <MessageRow author="Ada Lovelace" actions={<MessageActions><button type="button">Reply</button></MessageActions>}>
          Shipping today.
        </MessageRow>
      </ul>,
    );

    const actions = screen.getByRole("button", { name: "Reply" }).parentElement!;
    expect(actions.className.split(" ")).toEqual(expect.arrayContaining([
      "opacity-0", "group-hover:opacity-100", "focus-within:opacity-100", "pointer-coarse:opacity-100",
    ]));
  });
});
