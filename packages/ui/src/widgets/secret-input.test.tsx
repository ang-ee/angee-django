// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { AppRuntimeProvider } from "../runtime";
import { LabeledDescriptorField } from "../views/form/DescriptorFieldList";
import { passwordWidget } from "./scalarText";
import { textareaWidget } from "./textarea";

afterEach(cleanup);

test.each(["current-password", "new-password"])("descriptor password fields pass %s to the native control", (autoComplete) => {
  render(<AppRuntimeProvider runtime={{ widgets: { password: passwordWidget } }}>
    <LabeledDescriptorField field={{ name: "password", label: "Password", widget: "password", autoComplete }}
      value="" onChange={vi.fn()} />
  </AppRuntimeProvider>);
  const input = screen.getByLabelText<HTMLInputElement>("Password");
  expect(input.type).toBe("password");
  expect(input.autocomplete).toBe(autoComplete);
});

test("a descriptor secret textarea preserves pasted line breaks and autocomplete", () => {
  const onChange = vi.fn();
  render(<AppRuntimeProvider runtime={{ widgets: { textarea: textareaWidget } }}>
    <LabeledDescriptorField field={{ name: "private_key", label: "Private key", widget: "textarea", autoComplete: "new-password" }}
      value="" onChange={onChange} />
  </AppRuntimeProvider>);
  const input = screen.getByLabelText<HTMLTextAreaElement>("Private key");
  expect(input.tagName).toBe("TEXTAREA");
  expect(input.autocomplete).toBe("new-password");
  const key = "first line\nsecond line\nthird line\n";
  fireEvent.change(input, { target: { value: key } });
  expect(onChange).toHaveBeenCalledExactlyOnceWith(key);
});
