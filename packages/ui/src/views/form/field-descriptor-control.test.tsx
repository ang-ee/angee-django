// @vitest-environment happy-dom

import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import { AppRuntimeProvider } from "../../runtime";
import type { WidgetRenderProps } from "../../widgets";
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
});
