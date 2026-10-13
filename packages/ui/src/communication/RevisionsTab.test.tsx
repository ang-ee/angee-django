// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test } from "vitest";

import { RevisionsTab } from "./RevisionsTab";

describe("RevisionsTab", () => {
  afterEach(() => {
    cleanup();
  });

  test("states that revisions are retired and where a record's changes are", () => {
    render(<RevisionsTab resource="notes.Note" recordId="1" />);

    expect(screen.getByText("Revisions unavailable")).toBeTruthy();
    expect(screen.getByText(/History tab/)).toBeTruthy();
  });
});
