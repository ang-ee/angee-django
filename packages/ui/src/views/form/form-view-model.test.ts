import { describe, expect, test } from "vitest";

import type { FieldDescriptor } from "../page";
import {
  emptyDraft,
  addFieldSelection,
  formViewFieldLayout,
  missingRequiredFieldNames,
  mutationData,
  recordToValues,
  titleText,
  recordSubtitleParts,
} from "./form-view-model";

const fields: readonly FieldDescriptor[] = [
  { name: "config.local_root", widget: "text" },
  { name: "config.local_name", widget: "text" },
];

test("titleText preserves string and numeric scalar titles", () => {
  expect(titleText("Daily briefing", "Untitled")).toBe("Daily briefing");
  expect(titleText(42, "Untitled")).toBe("42");
});

test("keeps long text fields in declaration order when they opt out of body placement", () => {
  const addressFields: readonly FieldDescriptor[] = [
    { name: "label", title: true },
    { name: "street", widget: "textarea", body: false },
    { name: "extended", widget: "textarea", body: false },
    { name: "po_box" },
    { name: "city" },
    { name: "region" },
    { name: "postal_code" },
    { name: "country" },
    { name: "is_primary", widget: "switch" },
  ];

  const layout = formViewFieldLayout(addressFields, addressFields, [], null);

  expect(layout.bodyField).toBeUndefined();
  expect(layout.gridFields.map((field) => field.name)).toEqual([
    "street",
    "extended",
    "po_box",
    "city",
    "region",
    "postal_code",
    "country",
    "is_primary",
  ]);
});

test("object fields select only their declared row projection paths", () => {
  const paths = new Set<string>();
  addFieldSelection(
    paths,
    { name: "payload" },
    undefined,
    { name: "payload", kind: "object" },
    {
      kind: "object", scalar: null, values: [], nullable: true,
      row: { path: "payload", paths: ["payload.kind", "payload.target.id"] },
      filter: null, sort: null, relation: null,
    },
  );
  expect([...paths]).toEqual(["payload.kind", "payload.target.id"]);
});

describe("dotted form fields", () => {
  test("seed and read nested record values through native RHF-shaped data", () => {
    expect(emptyDraft(fields, { config: { local_root: "/srv/repo" } })).toEqual({
      config: { local_root: "/srv/repo", local_name: "" },
    });
    expect(recordToValues({ config: { local_root: "/repo", local_name: "main" } }, fields))
      .toEqual({ config: { local_root: "/repo", local_name: "main" } });
    expect(emptyDraft([
      { name: "config", widget: "json" },
      { name: "config.local_root", widget: "text", defaultValue: "/default" },
    ])).toEqual({ config: { local_root: "/default" } });
  });

  test("does not seed hidden implementation config before an implementation is selected", () => {
    const conditional: FieldDescriptor = {
      name: "config.local_root",
      widget: "text",
      defaultValue: "/default",
      showWhen: (values) => values.backend_class === "local",
    };
    expect(emptyDraft([
      { name: "backend_class", widget: "select" },
      { name: "config", widget: "json", showWhen: (values) => !values.backend_class },
      conditional,
    ])).toEqual({ backend_class: "", config: {} });
  });

  test("resolves visibility from the complete baseline regardless of declaration order", () => {
    expect(emptyDraft([
      {
        name: "config.local_root", widget: "text", defaultValue: "/default",
        showWhen: (values) => values.backend_class === "local",
      },
      { name: "backend_class", widget: "select", defaultValue: "local" },
    ])).toEqual({ config: { local_root: "/default" }, backend_class: "local" });
    expect(recordToValues(
      { backend_class: "local", config: { local_root: "/record" } },
      [
        { name: "config.local_root", widget: "text", showWhen: (values) => values.backend_class === "local" },
        { name: "backend_class", widget: "select" },
      ],
    )).toEqual({ config: { local_root: "/record" }, backend_class: "local" });
  });

  test("does not mutate nested caller defaults while building the evaluation baseline", () => {
    const config = Object.freeze({ local_root: "/caller" });
    const defaults = Object.freeze({ backend_class: "local", config });
    expect(emptyDraft([
      { name: "config", widget: "json" },
      { name: "config.local_name", widget: "text", defaultValue: "main" },
      { name: "backend_class", widget: "select" },
    ], defaults)).toEqual({
      config: { local_root: "/caller", local_name: "main" },
      backend_class: "local",
    });
    expect(defaults).toEqual({ backend_class: "local", config: { local_root: "/caller" } });
  });

  test("uses the selected descriptor required flag", () => {
    const dynamic: FieldDescriptor = {
      name: "config.shared",
      resolve: (values) => ({
        name: "config.shared",
        required: values.backend_class === "strict",
      }),
    };
    expect(missingRequiredFieldNames(
      { backend_class: "strict", config: { shared: "" } }, [dynamic], new Set(),
    )).toEqual(["config.shared"]);
    expect(missingRequiredFieldNames(
      { backend_class: "loose", config: { shared: "" } }, [dynamic], new Set(),
    )).toEqual([]);
  });

  test("accepts explicit null only for a nullable required descriptor", () => {
    expect(missingRequiredFieldNames(
      { note: null }, [{ name: "note", required: true, nullable: true, presenceRequired: true }], new Set(),
    )).toEqual([]);
    expect(missingRequiredFieldNames(
      { note: null }, [{ name: "note", required: true }], new Set(),
    )).toEqual(["note"]);
  });

  test("hydrates explicit null and omission without changing their presence", () => {
    const presenceFields: FieldDescriptor[] = [
      { name: "note", nullable: true, omittable: true },
      { name: "settings", kind: "object", nullable: true, omittable: true },
      { name: "summary", omittable: true },
    ];

    expect(recordToValues({ note: null, settings: null }, presenceFields)).toEqual({
      note: null,
      settings: null,
    });
  });

  test("uses JSON presence and declared constraints only when explicitly projected", () => {
    const presenceFields: FieldDescriptor[] = [
      { name: "title", required: true, presenceRequired: true },
      { name: "items", required: true, presenceRequired: true, kind: "array" },
    ];
    expect(missingRequiredFieldNames({ title: "", items: [] }, presenceFields, new Set())).toEqual([]);
    expect(missingRequiredFieldNames({ title: "" }, presenceFields, new Set())).toEqual(["items"]);
    expect(missingRequiredFieldNames(
      { title: "", items: [] }, [{ ...presenceFields[0]!, minLength: 1 }, { ...presenceFields[1]!, minItems: 1 }], new Set(),
    )).toEqual(["title", "items"]);
    expect(missingRequiredFieldNames(
      { title: "" }, [{ name: "title", required: true }], new Set(),
    )).toEqual(["title"]);
    const optional = { name: "summary", omittable: true, minLength: 2 };
    expect(missingRequiredFieldNames({}, [optional], new Set())).toEqual([]);
    expect(missingRequiredFieldNames({ summary: "" }, [optional], new Set())).toEqual(["summary"]);
  });

  test("submits nested config through its writable root and nested dirty state", () => {
    expect(mutationData(
      { config: { local_root: "/repo", local_name: "main" } },
      fields,
      {
        dirtyFields: { config: { local_root: true, local_name: true } },
        isCreate: true,
        writableFields: new Set(["config"]),
      },
    )).toEqual({ config: { local_root: "/repo", local_name: "main" } });
  });

  test("preserves visible config siblings when one nested field changes", () => {
    expect(mutationData(
      { config: { local_root: "/repo", local_name: "renamed" } },
      fields,
      {
        dirtyFields: { config: { local_name: true } },
        id: "bridge-1",
        isCreate: false,
        writableFields: new Set(["config"]),
      },
    )).toEqual({
      id: "bridge-1",
      config: { local_root: "/repo", local_name: "renamed" },
    });
  });

  test("does not submit nested config for empty or all-false dirty groups", () => {
    for (const configDirty of [{}, { local_root: false, local_name: false }]) {
      expect(mutationData(
        { config: { local_root: "/repo", local_name: "main" } }, fields,
        {
          dirtyFields: { config: configDirty }, id: "bridge-1", isCreate: false,
          writableFields: new Set(["config"]),
        },
      )).toEqual({ id: "bridge-1" });
    }
  });

  test("submits cleared numeric edits as null without inventing zero", () => {
    expect(mutationData(
      { count: "", ratio: "" },
      [
        { name: "count", widget: "integer" },
        { name: "ratio", widget: "float" },
      ],
      {
        dirtyFields: { count: true, ratio: true },
        id: "record-1",
        isCreate: false,
      },
    )).toEqual({ id: "record-1", count: null, ratio: null });
  });
});

describe("recordSubtitleParts", () => {
  const t = ((key: string, vars?: Record<string, unknown>) =>
    `${key}:${String(vars?.value ?? "")}`) as never;

  test("never puts an internal id in the record header", () => {
    // The header led with the sqid, which names the row for the database and
    // tells the reader nothing. A record's human key is what identifies it, and
    // it is shown where it exists -- cards, the list's Key column, triage.
    const parts = recordSubtitleParts(
      { id: "tsk_2KY5XM6f", created_at: "2026-09-01T10:00:00Z" },
      "tsk_2KY5XM6f",
      { created: "created_at", updated: null, wordCount: null },
      t,
    );

    expect(parts.join(" ")).not.toContain("tsk_2KY5XM6f");
    expect(parts).toHaveLength(1);
    expect(String(parts[0])).toContain("form.created");
  });

  test("still carries the facts the subtitle vocabulary names", () => {
    const parts = recordSubtitleParts(
      { id: "tsk_1", created_at: "2026-09-01T10:00:00Z", updated_at: "2026-09-02T10:00:00Z" },
      "tsk_1",
      { created: "created_at", updated: "updated_at", wordCount: null },
      t,
    );
    expect(parts).toHaveLength(2);
  });

  test("a record with no subtitle facts has no subtitle at all", () => {
    const parts = recordSubtitleParts(
      { id: "tsk_1" },
      "tsk_1",
      { created: null, updated: null, wordCount: null },
      t,
    );
    expect(parts).toEqual([]);
  });
});
