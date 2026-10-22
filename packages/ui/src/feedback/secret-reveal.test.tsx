// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test } from "vitest";

import { ModalsHost } from "./ModalsHost";
import { useSecretReveal } from "./secret-reveal";

afterEach(() => cleanup());

function RevealSignIn() {
  const reveal = useSecretReveal();
  return (
    <button onClick={() => void reveal({
      title: "Sign-in details",
      body: "Copy these before closing.",
      secrets: [
        { name: "username", label: "Username", value: "ada" },
        { name: "password", label: "Password", value: "once-only" },
      ],
      copy: { label: "Copy sign-in details", value: "ada / once-only" },
    })}>Reveal</button>
  );
}

describe("useSecretReveal", () => {
  test("opens a read-only prompt with one copyable field per secret and the combined copy control", async () => {
    render(<ModalsHost><RevealSignIn /></ModalsHost>);
    fireEvent.click(screen.getByRole("button", { name: "Reveal" }));
    const username = await screen.findByRole("textbox", { name: "Username" });
    expect((username as HTMLInputElement).readOnly).toBe(true);
    expect((username as HTMLInputElement).value).toBe("ada");
    expect((screen.getByRole("textbox", { name: "Password" }) as HTMLInputElement).value).toBe("once-only");
    expect(screen.getAllByRole("button", { name: "Copy" })).toHaveLength(2);
    expect(screen.getByRole("button", { name: "Copy sign-in details" })).toBeTruthy();
    expect(screen.getByText("Copy these before closing.")).toBeTruthy();
  });
});
