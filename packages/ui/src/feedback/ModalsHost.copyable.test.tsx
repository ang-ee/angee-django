// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import { ModalsHost, usePrompt, type PromptField } from "./ModalsHost";

const secret = "single-use-test-value";
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

function OpenPrompt({ field }: { field: PromptField }) {
  const prompt = usePrompt();
  return <button onClick={() => void prompt({ title: "Reveal result", fields: [field] })}>Reveal value</button>;
}

async function reveal(field: Partial<PromptField> = {}) {
  render(<ModalsHost><OpenPrompt field={{
    name: "value", label: "Returned value", defaultValue: secret, readOnly: true, copyable: true, ...field,
  }} /></ModalsHost>);
  fireEvent.click(screen.getByRole("button", { name: "Reveal value" }));
  return screen.findByRole("textbox", { name: "Returned value" });
}

function clipboard(writeText: (text: string) => Promise<void>) {
  vi.spyOn(navigator, "clipboard", "get").mockReturnValue({ writeText } as Clipboard);
}

describe("read-only prompt clipboard copying", () => {
  test("copies the exact value and announces success in a sibling status element", async () => {
    const writeText = vi.fn(async (_text: string) => undefined);
    clipboard(writeText);
    const input = await reveal();
    expect((input as HTMLInputElement).readOnly).toBe(true);
    expect(screen.getAllByLabelText("Returned value")).toHaveLength(1);
    const copy = screen.getByRole("button", { name: "Copy" });
    fireEvent.click(copy);
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("Copied"));
    expect(writeText).toHaveBeenCalledExactlyOnceWith(secret);
    expect(copy.contains(screen.getByRole("status"))).toBe(false);
    expect(copy.textContent).toBe("Copy");
  });

  test("shows a failure banner, permits retry, and never logs the returned value", async () => {
    const logs = ["log", "info", "warn", "error", "debug"] as const;
    const spies = logs.map((method) => vi.spyOn(console, method).mockImplementation(() => undefined));
    const writeText = vi.fn<(value: string) => Promise<void>>()
      .mockRejectedValueOnce(new Error(`denied: ${secret}`))
      .mockResolvedValue(undefined);
    clipboard(writeText);
    await reveal();
    fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    expect(await screen.findByText("Could not copy. Select and copy the value manually.")).toBeTruthy();
    expect(screen.getByRole("status").textContent).toBe("");
    expect(document.body.textContent).not.toContain(secret);
    fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("Copied"));
    expect(screen.queryByText("Could not copy. Select and copy the value manually.")).toBeNull();
    for (const spy of spies) expect(JSON.stringify(spy.mock.calls)).not.toContain(secret);
  });

  test("disables duplicate copying until the clipboard promise settles", async () => {
    let complete: () => void = () => undefined;
    const pending = new Promise<void>((resolve) => { complete = resolve; });
    const writeText = vi.fn(() => pending);
    clipboard(writeText);
    await reveal();
    const button = screen.getByRole("button", { name: "Copy" });
    fireEvent.click(button);
    expect((button as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(button);
    expect(writeText).toHaveBeenCalledTimes(1);
    complete();
    await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
  });

  test("closing removes the value and reopening resets copied status", async () => {
    clipboard(vi.fn(async () => undefined));
    await reveal();
    fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("Copied"));
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    await waitFor(() => expect(screen.queryByDisplayValue(secret)).toBeNull());
    expect(screen.queryByRole("status")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Reveal value" }));
    await screen.findByDisplayValue(secret);
    expect(screen.getByRole("status").textContent).toBe("");
  });

  test("offers one labelled control copying a value composed from the revealed fields", async () => {
    const writeText = vi.fn(async (_text: string) => undefined);
    clipboard(writeText);
    function OpenComposed() {
      const prompt = usePrompt();
      return <button onClick={() => void prompt({
        title: "Reveal result",
        fields: [{ name: "value", label: "Returned value", defaultValue: secret, readOnly: true }],
        copy: { label: "Copy both values", value: `name\n${secret}` },
      })}>Reveal value</button>;
    }
    render(<ModalsHost><OpenComposed /></ModalsHost>);
    fireEvent.click(screen.getByRole("button", { name: "Reveal value" }));
    await screen.findByRole("textbox", { name: "Returned value" });
    expect(screen.queryByRole("button", { name: "Copy" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Copy both values" }));
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("Copied"));
    expect(writeText).toHaveBeenCalledExactlyOnceWith(`name\n${secret}`);
  });

  test.each([{ readOnly: false, copyable: true }, { readOnly: true, copyable: false }])(
    "offers copying only when both field flags allow it: %j", async (field) => {
      await reveal(field);
      expect(screen.queryByRole("button", { name: "Copy" })).toBeNull();
    },
  );
});
