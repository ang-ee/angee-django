import { describe, expect, test } from "vitest";

import type { FieldDescriptor } from "../page";
import {
  emptyDraft,
  missingRequiredFieldNames,
  mutationData,
  recordToValues,
  titleText,
} from "./form-view-model";

const fields: readonly FieldDescriptor[] = [
  { name: "config.local_root", widget: "text" },
  { name: "config.local_name", widget: "text" },
];

test("titleText preserves string and numeric scalar titles", () => {
  expect(titleText("Daily briefing", "Untitled")).toBe("Daily briefing");
  expect(titleText(42, "Untitled")).toBe("42");
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
});
