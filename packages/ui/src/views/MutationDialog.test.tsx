// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import { AppRuntimeProvider } from "../runtime";
import { defaultWidgets } from "../widgets";
import { MutationDialog } from "./MutationDialog";

describe("MutationDialog", () => {
  afterEach(cleanup);

  test("associates descriptor labels and descriptions with widget inputs", () => {
    render(
      <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
        <MutationDialog
          open
          onOpenChange={vi.fn()}
          title="Connect Telegram"
          fields={[
            {
              name: "api_hash",
              label: "API hash",
              widget: "password",
              description: "Create or copy your Telegram application keys.",
            },
          ]}
          submitLabel="Connect"
          onSubmit={vi.fn()}
        />
      </AppRuntimeProvider>,
    );

    const label = screen.getByText("API hash").closest("label");
    const input = screen.getByLabelText("API hash");
    const description = screen.getByText(
      "Create or copy your Telegram application keys.",
    );

    expect(label?.htmlFor).toBe(input.id);
    expect(input.id).not.toBe("");
    expect(input.getAttribute("aria-describedby")?.split(" ")).toContain(
      description.id,
    );
  });
});
