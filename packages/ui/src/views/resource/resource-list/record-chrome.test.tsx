// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { RecordHeaderActions } from "./record-chrome";

afterEach(cleanup);

test("record chrome has no collection view switcher", () => {
  render(<RecordHeaderActions navigation={null} smartButtons={[]} />);
  expect(screen.queryByRole("group", { name: "Record view switcher" })).toBeNull();
});
