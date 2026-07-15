import { execFileSync } from "node:child_process";
import {
  mkdtempSync,
  mkdirSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { afterEach, describe, expect, test } from "vitest";

const roots: string[] = [];

afterEach(() => {
  for (const root of roots.splice(0)) rmSync(root, { recursive: true });
});

describe("group operation codegen", () => {
  test("selects the exact count root with matching having", () => {
    const generated = generateActions(METADATA);
    expect(generated).toContain("having?: Record<string, unknown>;");
    expect(generated).toContain('"value": "notes_groups_count"');
    expect(generated).toContain('"value": "totalCount"');
    expect(generated).toContain('"value": "having"');
  });

  test.each([
    ["roots.groupsCount", { roots: { groups: "notes_groups" } }],
    [
      "typeNames.having",
      {
        roots: {
          groups: "notes_groups",
          groupsCount: "notes_groups_count",
        },
        typeNames: {
          filter: "notes_bool_exp",
          groupBySpec: "NoteGroupBySpec",
          groupOrder: "NoteGroupOrder",
        },
      },
    ],
  ])("rejects a grouped resource missing %s", (missingField, override) => {
    const resource = METADATA.angee.resources[0];
    const metadata = {
      angee: {
        resources: [{ ...resource, ...override }],
      },
    };

    expect(() => generateActions(metadata)).toThrow(
      `Grouped resource notes.Note is missing required ${missingField}`,
    );
  });
});

function generateActions(metadata: unknown): string {
  const root = mkdtempSync(path.join(tmpdir(), "angee-group-codegen-"));
  roots.push(root);
  const webRoot = path.join(root, "web");
  const runtime = path.join(root, "runtime");
  mkdirSync(path.join(runtime, "web"), { recursive: true });
  mkdirSync(path.join(runtime, "schemas"), { recursive: true });
  mkdirSync(webRoot, { recursive: true });
  writeFileSync(
    path.join(runtime, "web", "manifest.json"),
    JSON.stringify({ schema: 1, documentRoots: [], addonPackages: [] }),
  );
  writeFileSync(path.join(runtime, "schemas", "public.graphql"), SDL);
  writeFileSync(
    path.join(runtime, "schemas", "public.metadata.json"),
    JSON.stringify(metadata),
  );

  const bin = fileURLToPath(
    new URL("../bin/angee-web-codegen.mjs", import.meta.url),
  );
  execFileSync(process.execPath, [
    bin,
    "--web-root",
    webRoot,
    "--runtime",
    runtime,
  ]);

  return readFileSync(
    path.join(runtime, "gql", "public", "actions.ts"),
    "utf8",
  );
}

const SDL = `
  schema { query: Query }
  type Query {
    notes_groups(
      group_by: [NoteGroupBySpec!]!
      where: notes_bool_exp
      having: NoteHaving
      order_by: [NoteGroupOrder!]
      limit: Int
      offset: Int
    ): [notes_group!]!
    notes_groups_count(
      group_by: [NoteGroupBySpec!]!
      where: notes_bool_exp
      having: NoteHaving
    ): Int!
  }
  input NoteGroupBySpec { field: String! }
  input NoteGroupOrder { field: String! }
  input NoteHaving { count_gt: Int }
  input notes_bool_exp { status: String }
  type notes_group { key: NoteGroupKey!, aggregate: NoteAggregate! }
  type NoteGroupKey { status: String }
  type NoteAggregate { count: Int! }
`;

const METADATA = {
  angee: {
    resources: [
      {
        modelLabel: "notes.Note",
        roots: {
          groups: "notes_groups",
          groupsCount: "notes_groups_count",
        },
        typeNames: {
          filter: "notes_bool_exp",
          groupBySpec: "NoteGroupBySpec",
          groupOrder: "NoteGroupOrder",
          having: "NoteHaving",
        },
        groupDimensions: [{ key: "status" }],
        aggregateMeasures: [],
      },
    ],
  },
};
