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
  test("derives an ActionResult mutation with one required scalar argument", () => {
    const generated = generateActions(METADATA);

    expect(generated).toContain('"submit_channel_password"');
    expect(generated.match(/"value": "password"/g)).toHaveLength(3);
    expect(generated).toContain('"value": "String"');
  });

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

  test("expands a saved line relation to id plus its record representation", () => {
    const generated = generateActions(SAVE_METADATA);

    expect(generated).toMatch(
      /"value": "product"[\s\S]{0,2000}"value": "id"[\s\S]{0,2000}"value": "name"/,
    );
  });

  test("fails by name when codegen cannot resolve a relation representation", () => {
    const [entry] = SAVE_METADATA.angee.resources;
    const broken = {
      angee: {
        resources: entry ? [entry] : [],
      },
    };

    expect(() => generateActions(broken)).toThrow(
      /RelationRepresentationError/,
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
  schema { query: Query mutation: Mutation }
  type Mutation {
    submit_channel_password(id: ID!, password: String!): ActionResult!
  }
  type ActionResult {
    ok: Boolean!
    message: String!
    id: ID
    validation_errors: JSON
  }
  scalar JSON
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

const SAVE_METADATA = {
  angee: {
    resources: [
      {
        modelLabel: "sales.Order",
        roots: { save: "order_save" },
        fields: [],
        linesResource: {
          field: "lines",
          modelLabel: "sales.OrderLine",
          inputType: "OrderLineInput",
          fields: [
            {
              name: "product",
              kind: "relation",
              readable: true,
              relationModelLabel: "catalog.Product",
            },
          ],
        },
      },
      {
        modelLabel: "catalog.Product",
        recordRepresentation: "name",
        roots: {},
        fields: [
          { name: "name", kind: "scalar", readable: true },
        ],
      },
    ],
  },
};
