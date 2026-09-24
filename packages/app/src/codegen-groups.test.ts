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
import { buildSchema, executeSync, print, type DocumentNode } from "graphql";

import { afterEach, describe, expect, test } from "vitest";
import type { DataResourceFieldMetadata } from "@angee/metadata";
import { testDataResource, testResourceQuery, testQueryField, testQueryAxis } from "@angee/metadata/testing";

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

  test("derives domain target, enum, and list action arguments from the schema", () => {
    const generated = generateActions(METADATA);

    expect(generated).toContain('"close_round"');
    expect(generated).toContain('"value": "round"');
    expect(generated).toContain('"value": "RoundOutcome"');
    expect(generated).toContain('"value": "accepted"');
  });

  test("derives optional enum arguments and preserves schema defaults and nullability", () => {
    const generated = generateActions(METADATA);
    const schema = buildSchema(SDL);
    expect(print(generatedAction(generated, "defaulted_action"))).toContain('$note: String! = ""');
    expect(print(generatedAction(generated, "resolveSyncDiscrepancy"))).toContain("$keep: ConflictKeep = null");
    for (const [field, variables, expected] of [
      ["optional_action", { id: "record_1" }, { id: "record_1" }],
      ["resolveSyncDiscrepancy", { id: "record_1" }, { id: "record_1", keep: null }],
      ["resolveSyncDiscrepancy", { id: "record_1", keep: "REMOTE" }, { id: "record_1", keep: "REMOTE" }],
      ["resolveSyncDiscrepancy", { id: "record_1", keep: null }, { id: "record_1", keep: null }],
      ["defaulted_action", { id: "record_1" }, { id: "record_1", note: "" }],
      ["defaulted_action", { id: "record_1", note: "value" }, { id: "record_1", note: "value" }],
    ] as const) {
      const result = executeSync({
        schema,
        document: generatedAction(generated, field),
        variableValues: variables,
        rootValue: { [field]: (args: unknown) => ({ ok: true, message: JSON.stringify(args) }) },
      });
      expect(result.errors).toBeUndefined();
      expect(result.data?.[field]).toMatchObject({ ok: true, message: JSON.stringify(expected) });
    }
    for (const [field, variables] of [
      ["resolveSyncDiscrepancy", { id: "record_1", keep: "typo" }],
      ["defaulted_action", { id: "record_1", note: null }],
      ["resolveSyncDiscrepancy", {}],
    ] as const) {
      expect(executeSync({
        schema, document: generatedAction(generated, field), variableValues: variables,
      }).errors).toHaveLength(1);
    }
  });

  test("keeps optional actions outside the required id target contract authored", () => {
    const generated = generateActions(METADATA);

    expect(generated).toContain(
      'export type ActionFieldName = "close_round" | "defaulted_action" | "optional_action" | "resolveSyncDiscrepancy" | "submit_channel_password";',
    );
    for (const field of [
      "install", "disable", "start_workflow_run", "start_workflow_recovery",
      "optional_id", "defaulted_id", "string_id", "list_id", "extra_required_arg",
      "optional_only", "no_arguments",
    ]) {
      expect(() => generatedAction(generated, field)).toThrow(`Missing generated action: ${field}`);
    }
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
      /"value": "item"[\s\S]{0,2000}"value": "id"[\s\S]{0,2000}"value": "name"/,
    );
  });

  test("validates an axis-backed scalar relation as a leaf selection", () => {
    const generated = generateActions(SAVE_METADATA);

    expect(generated).toContain('"value": "owner"');
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

function generatedAction(generated: string, field: string): DocumentNode {
  const match = generated.match(new RegExp(`  "${field}": (\\{[\\s\\S]*?\\}) as ActionDocument<"${field}">`));
  if (!match?.[1]) throw new Error(`Missing generated action: ${field}`);
  return JSON.parse(match[1]) as DocumentNode;
}

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
    close_round(round: ID!, outcome: RoundOutcome!, accepted: [ID!]!): ActionResult!
    resolveSyncDiscrepancy(id: ID!, keep: ConflictKeep = null): ActionResult!
    optional_action(id: ID!, keep: ConflictKeep): ActionResult!
    defaulted_action(id: ID!, note: String! = ""): ActionResult!
    install(addon: String!, revision: String = null): ActionResult!
    disable(addon: String!, revision: String = null): ActionResult!
    start_workflow_run(workflow: ID!, subject: WorkflowObjectRefInput = null): ActionResult!
    start_workflow_recovery(source_attempt: ID!, request_key: String!, acknowledge_uncertain_external: Boolean! = false, prior_recovery: ID = null): ActionResult!
    optional_id(id: ID, keep: ConflictKeep): ActionResult!
    defaulted_id(id: ID! = "record_1", keep: ConflictKeep): ActionResult!
    string_id(id: String!, keep: ConflictKeep): ActionResult!
    list_id(id: [ID!]!, keep: ConflictKeep): ActionResult!
    extra_required_arg(id: ID!, password: String!, keep: ConflictKeep): ActionResult!
    optional_only(keep: ConflictKeep): ActionResult!
    no_arguments: ActionResult!
    document_save(pk: ID!, lines: [DocumentLineInput!]): DocumentType!
  }
  input WorkflowObjectRefInput { model: String!, id: ID! }
  enum RoundOutcome { AWARDED NO_AWARD }
  enum ConflictKeep { REMOTE LOCAL }
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
  input DocumentLineInput { item: ID }
  type ItemType { id: ID!, name: String! }
  type DocumentLineType { id: ID!, item: ItemType! }
  type DocumentType { id: ID!, owner: ID, lines: [DocumentLineType!]! }
`;

const METADATA = {
  angee: {
    resources: [
      testDataResource("notes.Note", {
        query: testResourceQuery({ identity: { field: "id" }, fields: { "id": testQueryField("id", { scalar: "ID", filter: null }),
                "status": testQueryField("status", { scalar: "String", filter: null }) }, axes: { "status": testQueryAxis("status", { kind: "column", identityPath: "status", paths: ["status"], server: { input: "status", key: "status" }, extractions: [], drill: null }) }, sort: { default: [] } }),

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

        aggregateMeasures: [],
      }),
    ],
  },
};

const SAVE_METADATA = {
  angee: {
    resources: [
      testDataResource("example.Document", {
        query: testResourceQuery({ identity: { field: "id" }, fields: { "owner": testQueryField("owner", { scalar: "ID", kind: "relation", filter: null, relation: { model: "accounts.User", identityPath: "owner" }, row: { path: "owner", paths: ["owner"] } }),
                "id": testQueryField("id", { scalar: "ID", filter: null }) }, axes: {}, sort: { default: [] } }),

        roots: { save: "document_save" },
        fields: [
          resourceField({
            name: "owner",
            kind: "relation",
            readable: true,
            relationModelLabel: "accounts.User",
            relationObject: false,
          }),
        ],

        linesResource: {
          field: "lines",
          modelLabel: "example.DocumentLine",
          inputType: "DocumentLineInput",
          fields: [
            resourceField({
              name: "item",
              kind: "relation",
              readable: true,
              relationModelLabel: "example.Item",
              relationObject: true,
            }),
          ],
        },
      }),
      testDataResource("example.Item", {
        recordRepresentation: "name",
        roots: {},
        fields: [
          resourceField({ name: "name", kind: "scalar", scalar: "String", readable: true }),
        ],
      }),
      testDataResource("accounts.User", {
        recordRepresentation: "username",
        roots: {},
        fields: [
          resourceField({
            name: "username",
            kind: "scalar",
            scalar: "String",
            readable: true,
          }),
        ],
      }),
    ],
  },
};

function resourceField(
  overrides: Pick<DataResourceFieldMetadata, "name" | "kind"> &
    Partial<DataResourceFieldMetadata>,
): DataResourceFieldMetadata {
  return { ...baseResourceField(), ...overrides };
}

function baseResourceField() {
  return {
    name: "field",
    kind: "scalar" as const,
    readable: false,

    aggregatable: false,

    creatable: false,
    updatable: false,
    requiredOnCreate: false,
  };
}
