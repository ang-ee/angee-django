// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test } from "vitest";

import { PopoverPortal, PopoverPositioner, PopoverRoot } from "./popover";

afterEach(cleanup);

describe("PopoverPositioner", () => {
  test("uses the shared popover stacking layer", () => {
    render(
      <PopoverRoot open>
        <PopoverPortal>
          <PopoverPositioner
            data-testid="positioner"
            className="custom-layer"
          />
        </PopoverPortal>
      </PopoverRoot>,
    );

    const positioner = screen.getByTestId("positioner");
    expect(positioner.className).toContain("z-popover");
    expect(positioner.className).toContain("custom-layer");
  });
});
