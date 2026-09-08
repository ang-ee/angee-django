// @vitest-environment happy-dom

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";

import { AppRuntimeProvider } from "../../runtime";
import { defaultWidgets, type WidgetFocusTarget, type WidgetRenderProps } from "../../widgets";
import { FieldDescriptorControl } from "./field-descriptor-control";

describe("FieldDescriptorControl", () => {
  test("passes the source row to a read widget", () => {
    render(
      <AppRuntimeProvider
        runtime={{
          widgets: {
            money: {
              read: ({ value, row }: WidgetRenderProps) => (
                <span>
                  {String(value)} {String((row as { currency?: string }).currency)}
                </span>
              ),
            },
          },
        }}
      >
        <FieldDescriptorControl
          field={{ name: "cost", widget: "money", currencyField: "currency" }}
          value="42"
          row={{ currency: "EUR" }}
          readOnly
        />
      </AppRuntimeProvider>,
    );

    expect(screen.getByText("42 EUR")).toBeTruthy();
  });

  test("focuses a select's real portal trigger", () => {
    const focusRef = vi.fn<(target: WidgetFocusTarget | null) => void>();
    render(
      <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
        <FieldDescriptorControl
          field={{ name: "status", widget: "select", label: "Status", options: [
            { value: "draft", label: "Draft" },
            { value: "published", label: "Published" },
          ] }}
          value="draft"
          onChange={() => undefined}
          controlRef={focusRef}
        />
      </AppRuntimeProvider>,
    );

    (focusRef.mock.calls.at(-1)?.[0] as WidgetFocusTarget).focus();
    const trigger = screen.getByRole("combobox", { name: "Status" });
    expect(document.activeElement).toBe(trigger);
    fireEvent.click(trigger);
    expect(screen.getByRole("option", { name: "Published" })).toBeTruthy();
  });
});
