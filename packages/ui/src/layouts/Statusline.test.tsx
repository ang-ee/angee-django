// @vitest-environment happy-dom

import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { AppRuntimeProvider } from "../runtime";

import { baseIcons } from "../chrome/icon-registry";
import { Statusline, StatusSegment, StatuslineSpacer } from "./Statusline";

function withIcons(node: React.ReactNode): React.ReactElement {
  return (
    <AppRuntimeProvider runtime={{ icons: baseIcons }}>{node}</AppRuntimeProvider>
  );
}

describe("Statusline", () => {
  test("renders segments inline when no layout host is provided", () => {
    render(
      withIcons(
        <Statusline>
          <StatusSegment icon="check" tone="success">
            Synced
          </StatusSegment>
          <StatuslineSpacer />
          <StatusSegment>console</StatusSegment>
        </Statusline>,
      ),
    );
    expect(screen.getByText("Synced")).toBeTruthy();
    expect(screen.getByText("console")).toBeTruthy();
  });

  test("a segment with onClick is a button", () => {
    render(withIcons(<StatusSegment onClick={() => undefined}>Save</StatusSegment>));
    expect(screen.getByRole("button", { name: "Save" })).toBeTruthy();
  });
});
