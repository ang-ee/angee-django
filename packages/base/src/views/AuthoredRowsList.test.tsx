// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import type { TypedDocumentNode } from "@angee/sdk";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { AuthoredRowsList } from "./AuthoredRowsList";
import type { ListColumn } from "./ListInternals";
import type { StringIdRow } from "./data-view-surface";

const sdkMocks = vi.hoisted(() => ({
  calls: [] as Array<{
    document: unknown;
    variables: unknown;
    options: unknown;
  }>,
}));

vi.mock("@angee/sdk", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/sdk")>();
  return {
    ...actual,
    useAuthoredRows: (
      document: unknown,
      options: unknown,
    ) => {
      const rowOptions = options as {
        variables?: unknown;
        selectRows: (data: NotesData | undefined) => readonly NoteRow[];
      };
      sdkMocks.calls.push({
        document,
        variables: rowOptions.variables,
        options,
      });
      const data = { notes: [{ id: "note-1", title: "Hello" }] };
      return {
        data,
        rows: rowOptions.selectRows(data),
        fetching: false,
        error: null,
        refetch: () => undefined,
      };
    },
  };
});

afterEach(() => cleanup());

beforeEach(() => {
  sdkMocks.calls = [];
});

interface NoteRow extends StringIdRow {
  title: string;
}

interface NotesData {
  notes: Array<{ id: string; title: string }>;
}

interface NotesVariables extends Record<string, unknown> {
  limit: number;
}

const document = {} as TypedDocumentNode<NotesData, NotesVariables>;

const columns: readonly ListColumn<NoteRow>[] = [{ field: "title" }];

describe("AuthoredRowsList", () => {
  test("runs an authored query, selects rows, and renders the shared rows view", async () => {
    render(
      <AuthoredRowsList
        scope="local"
        document={document}
        variables={{ limit: 2 }}
        queryOptions={{ models: ["notes.Note"] }}
        selectRows={(data) => data?.notes ?? []}
        columns={columns}
      />,
    );

    expect(await screen.findByText("Hello")).toBeTruthy();
    expect(sdkMocks.calls).toEqual([
      {
        document,
        variables: { limit: 2 },
        options: {
          models: ["notes.Note"],
          variables: { limit: 2 },
          selectRows: expect.any(Function),
        },
      },
    ]);
  });
});
