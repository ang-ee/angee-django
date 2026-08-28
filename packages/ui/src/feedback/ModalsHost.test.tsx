// @vitest-environment happy-dom

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, test } from "vitest";
import type { ReactElement } from "react";
import { useState } from "react";

import { ModalsHost, usePrompt } from "./ModalsHost";

describe("ModalsHost", () => {
  beforeAll(() => {
    Element.prototype.getAnimations ??= () => [];
  });

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
});

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
