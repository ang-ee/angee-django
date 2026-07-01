// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test } from "vitest";

import { MessageRow } from "./index";

describe("MessageRow", () => {
  afterEach(() => {
    cleanup();
  });

  test("renders the author, body, and every composed slot", () => {
    render(
      <ul>
        <MessageRow
          author="Ada Lovelace"
          timestamp="2026-07-01T10:00:00Z"
          channel={<span>Email · Inbound</span>}
          tracking={<div>Stage: Draft → In review</div>}
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
    expect(screen.getByText("Stage: Draft → In review")).toBeTruthy();
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
});
