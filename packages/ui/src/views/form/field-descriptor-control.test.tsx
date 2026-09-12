// @vitest-environment happy-dom

import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, test, vi } from "vitest";

import { AppRuntimeProvider } from "../../runtime";
import { defaultWidgets, type WidgetFocusTarget, type WidgetRenderProps } from "../../widgets";
import { FieldDescriptorControl } from "./field-descriptor-control";

describe("FieldDescriptorControl", () => {
  test("keeps the fallback text control mounted while its value changes", () => {
    function Harness() {
      const [value, setValue] = useState("");
      return (
        <AppRuntimeProvider runtime={{ widgets: {} }}>
          <FieldDescriptorControl
            field={{ name: "city", label: "City" }}
            value={value}
            onChange={(next) => setValue(String(next ?? ""))}
          />
        </AppRuntimeProvider>
      );
    }

    render(<Harness />);
    const input = screen.getByRole("textbox", { name: "City" }) as HTMLInputElement;
    input.focus();

    fireEvent.change(input, { target: { value: "A" } });
    expect(screen.getByRole("textbox", { name: "City" })).toBe(input);
    expect(document.activeElement).toBe(input);

    fireEvent.change(input, { target: { value: "Am" } });
    expect(input.value).toBe("Am");
    expect(document.activeElement).toBe(input);
  });

  test("passes the source row to a read widget", () => {
    render(
      <AppRuntimeProvider
        runtime={{
          widgets: {
            money: {
              read: ({ value, row, parentRow }: WidgetRenderProps) => (
                <span>
                  {String(value)} {String((row as { currency?: string }).currency)} {String((parentRow as { company?: string }).company)}
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
          parentRow={{ company: "Acme" }}
          readOnly
        />
      </AppRuntimeProvider>,
    );

    expect(screen.getByText("42 EUR Acme")).toBeTruthy();
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
