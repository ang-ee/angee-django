// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import { AppRuntimeProvider } from "../runtime";
import { defaultWidgets } from "../widgets";
import {
  LabeledDescriptorField,
  MutationDialog,
  emptyValueForField,
} from "./MutationDialog";

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

  test("binds server messages to the matching shared field control", () => {
    render(
      <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
        <LabeledDescriptorField
          field={{ name: "title", label: "Title" }}
          value=""
          messages={["This field is required."]}
          onChange={vi.fn()}
        />
      </AppRuntimeProvider>,
    );

    const input = screen.getByLabelText("Title");
    const message = screen.getByText("This field is required.");

    expect(input.getAttribute("aria-describedby")?.split(" ")).toContain(
      message.id,
    );
    expect(message.closest('[data-invalid=""]')).not.toBeNull();
  });

  test("uses schema-safe empty values for descriptor field kinds", () => {
    expect(emptyValueForField({ kind: "integer" })).toBeNull();
    expect(emptyValueForField({ kind: "number" })).toBeNull();
    expect(emptyValueForField({ kind: "any" })).toBeNull();
    expect(emptyValueForField({ kind: "array" })).toEqual([]);
    expect(emptyValueForField({ kind: "object" })).toEqual({});
    expect(emptyValueForField({ kind: "boolean" })).toBe(false);
    expect(emptyValueForField({ kind: "any", widget: "select" })).toBe("");
    expect(emptyValueForField({ kind: "string" })).toBe("");
  });
});
