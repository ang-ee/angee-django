// @vitest-environment happy-dom

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { DatePopover } from "./date-popover";

describe("DatePopover", () => {
  it("offers native month and year selection around the active date", () => {
    render(
      <DatePopover
        selected={new Date(2024, 3, 9)}
        label="April 9, 2024"
        ariaLabel="Invoice date"
        open
        onOpenChange={vi.fn()}
        onSelectDate={vi.fn()}
      />,
    );

    const month = screen.getByRole("combobox", { name: /month/i }) as HTMLSelectElement;
    const year = screen.getByRole("combobox", { name: /year/i }) as HTMLSelectElement;
    expect(month.value).toBe("3");
    expect(year.value).toBe("2024");
  });
});
