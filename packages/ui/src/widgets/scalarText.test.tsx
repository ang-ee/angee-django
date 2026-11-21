// @vitest-environment happy-dom

import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import { passwordWidget } from "./scalarText";

const Edit = passwordWidget.edit;
const Read = passwordWidget.read;

describe("password widget", () => {
  test("enters a secret masked, with the hint that keeps the person's saved sign-in out of it", () => {
    render(<Edit value="" field={{ label: "Service key", placeholder: "Paste the key" }} />);
    const input = screen.getByLabelText("Service key") as HTMLInputElement;
    expect(input.type).toBe("password");
    expect(input.getAttribute("autocomplete")).toBe("new-password");
    expect(input.placeholder).toBe("Paste the key");
  });

  test("reads a value as a mask and no value as nothing", () => {
    const { container, rerender } = render(<Read value="invented-secret" />);
    expect(container.textContent).toBe("••••••••");
    rerender(<Read value="" />);
    expect(container.textContent).toBe("");
    rerender(<Read value={null} />);
    expect(container.textContent).toBe("");
  });
});
