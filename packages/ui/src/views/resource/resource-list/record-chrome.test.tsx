// @vitest-environment happy-dom

import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { RecordHeaderActions } from "./record-chrome";

test("record chrome has no collection view switcher", () => {
  render(<RecordHeaderActions navigation={null} smartButtons={[]} />);
  expect(screen.queryByRole("group", { name: "Record view switcher" })).toBeNull();
});
