// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { createUiTestProviders } from "@angee/ui/testing";
import { afterEach, expect, test, vi } from "vitest";

import { MessageReplyQuote } from "./MessageReplyQuote";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const message = { preview: "Earlier message", sender: { display_name: "Dane" } };

test("a reply link retains link semantics and host routing without native-button errors", () => {
  const navigate = vi.fn();
  const errors = vi.spyOn(console, "error").mockImplementation(() => undefined);
  const providers = createUiTestProviders({ navigate });
  render(<MessageReplyQuote message={message} href="/messages/parent" />, { wrapper: providers.Provider });

  const link = screen.getByRole("link", { name: "Replying to Dane Earlier message" });
  expect(link.tagName).toBe("A");
  expect(link.getAttribute("href")).toBe("/messages/parent");
  fireEvent.click(link);
  expect(navigate).toHaveBeenCalledExactlyOnceWith("/messages/parent");
  expect(fireEvent.click(link, { ctrlKey: true })).toBe(true);
  expect(navigate).toHaveBeenCalledTimes(1);
  expect(errors).not.toHaveBeenCalled();
});

test("a host-controlled reply uses a native button and keeps clearing independent", () => {
  const onOpen = vi.fn();
  const onClear = vi.fn();
  render(<MessageReplyQuote message={message} onOpen={onOpen} onClear={onClear} />);

  const button = screen.getByRole("button", { name: "Replying to Dane Earlier message" });
  expect(button.tagName).toBe("BUTTON");
  expect(button.getAttribute("type")).toBe("button");
  fireEvent.click(button);
  expect(onOpen).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "Cancel reply" }));
  expect(onClear).toHaveBeenCalledTimes(1);
  expect(onOpen).toHaveBeenCalledTimes(1);
});
