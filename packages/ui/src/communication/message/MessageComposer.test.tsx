// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test } from "vitest";

import { MessageAttachmentChip, MessageComposer, MessageComposerHint } from "./index";

describe("MessageComposer", () => {
  afterEach(() => {
    cleanup();
  });

  test("frames the input slot, attachment chips, hint, and actions", () => {
    render(
      <MessageComposer
        input={<textarea aria-label="Message" />}
        attachments={<MessageAttachmentChip>screenshot.png</MessageAttachmentChip>}
        hint={<MessageComposerHint />}
        actions={<button type="button">Send</button>}
      />,
    );

    expect(screen.getByRole("textbox", { name: "Message" })).toBeTruthy();
    expect(screen.getByText("screenshot.png")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Send" })).toBeTruthy();
  });

  test("defaults the hint to the send / newline keyboard affordance", () => {
    render(<MessageComposer hint={<MessageComposerHint />} />);

    // Provider-less, the base bundle resolves the default hint copy.
    expect(screen.getByText("send")).toBeTruthy();
    expect(screen.getByText("newline")).toBeTruthy();
  });

  test("falls back to a plain textarea when no input slot is given", () => {
    render(<MessageComposer />);

    expect(screen.getByRole("textbox")).toBeTruthy();
  });
});
