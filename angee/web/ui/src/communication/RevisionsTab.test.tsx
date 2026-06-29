// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { RevisionsTab } from "./RevisionsTab";

interface RevisionsResult {
  revisions: readonly Record<string, unknown>[];
  count: number;
  fetching: boolean;
  error: Error | null;
  refetch: () => void;
}

const dataMocks = vi.hoisted(() => ({
  useResourceRevisions: vi.fn(),
}));

vi.mock("../data/revisions", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../data/revisions")>();
  return {
    ...actual,
    useResourceRevisions: dataMocks.useResourceRevisions,
  };
});

describe("RevisionsTab", () => {
  afterEach(() => {
    cleanup();
  });

  beforeEach(() => {
    dataMocks.useResourceRevisions.mockReset();
    dataMocks.useResourceRevisions.mockReturnValue(revisionsResult());
  });

  test("renders the loading state", () => {
    dataMocks.useResourceRevisions.mockReturnValue(
      revisionsResult({ fetching: true }),
    );

    render(<RevisionsTab resource="notes.Note" recordId="1" />);

    expect(screen.getByRole("status").textContent).toContain("Loading revisions");
  });

  test("renders the error state", () => {
    dataMocks.useResourceRevisions.mockReturnValue(
      revisionsResult({ error: new Error("Revision query failed") }),
    );

    render(<RevisionsTab resource="notes.Note" recordId="1" />);

    expect(screen.getByText("Revisions unavailable")).toBeTruthy();
    expect(screen.getByText("Revision query failed")).toBeTruthy();
  });

  test("renders the empty state", () => {
    render(<RevisionsTab resource="notes.Note" recordId="1" />);

    expect(screen.getByText("No revisions yet")).toBeTruthy();
  });

  test("renders revision entries", () => {
    dataMocks.useResourceRevisions.mockReturnValue(
      revisionsResult({
        revisions: [
          {
            id: "v1",
            created_at: "2026-01-01T00:00:00Z",
            comment: "Body changed",
            title: "Snapshot title",
          },
        ],
      }),
    );

    render(<RevisionsTab resource="notes.Note" recordId="1" />);

    expect(screen.getByText("Body changed")).toBeTruthy();
    expect(screen.getByText("Snapshot title")).toBeTruthy();
  });
});

function revisionsResult(overrides: Partial<RevisionsResult> = {}): RevisionsResult {
  const revisions = overrides.revisions ?? [];
  return {
    revisions,
    count: revisions.length,
    fetching: false,
    error: null,
    refetch: vi.fn(),
    ...overrides,
  };
}
