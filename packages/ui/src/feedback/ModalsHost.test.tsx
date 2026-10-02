// @vitest-environment happy-dom

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, test } from "vitest";
import type { ReactElement } from "react";
import { useState } from "react";

import { ModalsHost, useConfirm, usePrompt } from "./ModalsHost";

describe("ModalsHost", () => {
  afterEach(() => {
    cleanup();
  });

  test("captures prompt input values before React clears the event target", async () => {
    render(
      <ModalsHost>
        <PromptButton />
      </ModalsHost>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Open prompt" }));

    const input = await screen.findByRole("textbox", {
      name: "Authorization code",
    });
    fireEvent.change(input, { target: { value: "code#state" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));

    await waitFor(() => {
      expect(screen.getByText("code#state")).toBeTruthy();
    });
  });

  test("keeps structured confirmation content inside a block description", async () => {
    render(<ModalsHost><ConfirmButton /></ModalsHost>);
    fireEvent.click(screen.getByRole("button", { name: "Open confirmation" }));
    const list = await screen.findByRole("list");
    expect(list.closest("p")).toBeNull();
    expect(list.parentElement?.tagName).toBe("DIV");
  });
});

function ConfirmButton(): ReactElement {
  const confirm = useConfirm();
  return <button type="button" onClick={() => { void confirm({
    title: "Enable trigger", body: <><p>Principal grants</p><ul><li>Read channel</li></ul></>,
  }); }}>Open confirmation</button>;
}

function PromptButton(): ReactElement {
  const prompt = usePrompt();
  const [value, setValue] = useState("");

  return (
    <>
      <button
        type="button"
        onClick={() => {
          void prompt({
            title: "Connect account",
            fields: [
              {
                name: "pasted",
                label: "Authorization code",
                placeholder: "code#state",
              },
            ],
          }).then((result) => setValue(result?.pasted ?? ""));
        }}
      >
        Open prompt
      </button>
      <output>{value}</output>
    </>
  );
}
