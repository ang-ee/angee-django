// @vitest-environment happy-dom

import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import { textareaWidget } from "./textarea";

const Edit = textareaWidget.edit;

describe("textarea widget", () => {
  test("shows the field's placeholder, else its label", () => {
    const { rerender } = render(<Edit value="" field={{ label: "Key file", placeholder: "Paste the key file" }} />);
    expect((screen.getByLabelText("Key file") as HTMLTextAreaElement).placeholder).toBe("Paste the key file");
    rerender(<Edit value="" field={{ label: "Notes" }} />);
    expect((screen.getByLabelText("Notes") as HTMLTextAreaElement).placeholder).toBe("Notes");
  });
});
