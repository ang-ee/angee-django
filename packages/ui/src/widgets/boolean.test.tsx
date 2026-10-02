// @vitest-environment happy-dom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";

import { AppRuntimeProvider } from "../runtime";
import { LabeledDescriptorField } from "../views/form/DescriptorFieldList";
import { booleanWidget } from "./boolean";

afterEach(cleanup);

test.each([false, true])("associates a boolean field's error with its actual checkbox (readOnly=%s)", (readOnly) => {
  render(<AppRuntimeProvider runtime={{ widgets: { boolean: booleanWidget } }}>
    <LabeledDescriptorField field={{ name: "acknowledge", label: "Acknowledge", widget: "boolean" }}
      value={false} messages={["Acknowledgement is required."]} readOnly={readOnly} onChange={() => undefined} />
  </AppRuntimeProvider>);
  const checkbox = screen.getByRole("checkbox", { name: "Acknowledge" });
  const error = screen.getByText("Acknowledgement is required.");
  expect(checkbox.getAttribute("aria-invalid")).toBe("true");
  expect(checkbox.getAttribute("aria-describedby")?.split(" ").some((id) => document.getElementById(id)?.contains(error))).toBe(true);
  const labelTarget = screen.getByText("Acknowledge").getAttribute("for");
  expect(document.getElementById(labelTarget ?? "")?.getAttribute("type")).toBe("checkbox");
});
