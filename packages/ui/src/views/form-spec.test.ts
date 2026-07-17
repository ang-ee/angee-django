import { describe, expect, test } from "vitest";

import { defaultWidgets } from "../widgets";
import {
  deserializeFormSpec,
  formSpecInitialValues,
} from "./form-spec";

describe("deserializeFormSpec", () => {
  test("maps the recursive backend schema and its data-only UI extensions", () => {
    const fields = deserializeFormSpec(
      {
        type: "object",
        required: ["title", "target", "rows"],
        properties: {
          title: {
            type: "string",
            label: "Title",
            description: "Human-readable title",
            placeholder: "Review import",
            defaultValue: "Untitled",
          },
          count: { type: "integer" },
          confidence: { type: "number" },
          approved: { type: "boolean" },
          config: { type: "object" },
          tags: { type: "array", items: { type: "string" } },
          mode: {
            enum: ["append", "replace"],
            options: [
              { value: "append", label: "Append" },
              { value: "replace", label: "Replace", disabled: true },
            ],
          },
          target: {
            type: "string",
            relation: {
              resource: "Channel",
              labelField: "name",
              filters: [{ field: "status", operator: "eq", value: "active" }],
              create: { resource: "Channel" },
            },
          },
          rows: {
            type: "array",
            items: {
              type: "object",
              required: ["target"],
              properties: {
                target: {
                  type: "string",
                  relation: { resource: "Channel" },
                },
                replace: { type: "boolean", widget: "switch" },
              },
            },
          },
        },
      },
      defaultWidgets,
    );

    expect(fields).toEqual([
      {
        name: "title",
        kind: "string",
        widget: "text",
        label: "Title",
        description: "Human-readable title",
        placeholder: "Review import",
        required: true,
        defaultValue: "Untitled",
      },
      { name: "count", kind: "integer", widget: "integer" },
      { name: "confidence", kind: "number", widget: "float" },
      { name: "approved", kind: "boolean", widget: "boolean" },
      { name: "config", kind: "object", widget: "json" },
      { name: "tags", kind: "array", widget: "json" },
      {
        name: "mode",
        kind: "any",
        widget: "select",
        options: [
          { value: "append", label: "Append" },
          { value: "replace", label: "Replace", disabled: true },
        ],
      },
      {
        name: "target",
        kind: "string",
        widget: "many2one",
        required: true,
        relation: {
          resource: "Channel",
          labelField: "name",
          filters: [{ field: "status", operator: "eq", value: "active" }],
          create: { resource: "Channel" },
        },
      },
      {
        name: "rows",
        kind: "array",
        widget: "rows",
        required: true,
        rowTemplate: [
          {
            name: "target",
            kind: "string",
            widget: "many2one",
            required: true,
            relation: { resource: "Channel" },
          },
          {
            name: "replace",
            kind: "boolean",
            widget: "switch",
          },
        ],
      },
    ]);
  });

  test("derives select options from an enum", () => {
    expect(
      deserializeFormSpec(
        {
          type: "object",
          properties: { mode: { enum: ["append", "replace"] } },
        },
        defaultWidgets,
      ),
    ).toEqual([
      {
        name: "mode",
        kind: "any",
        widget: "select",
        options: [
          { value: "append", label: "append" },
          { value: "replace", label: "replace" },
        ],
      },
    ]);
  });

  test("rejects enum values the string-valued select cannot preserve", () => {
    expect(() =>
      deserializeFormSpec(
        {
          type: "object",
          properties: { priority: { enum: [1, 2] } },
        },
        defaultWidgets,
      ),
    ).toThrowError(
      "Invalid priority.enum: form-spec select values must be strings.",
    );
  });

  test("throws instead of silently falling back for an unknown widget", () => {
    expect(() =>
      deserializeFormSpec(
        {
          type: "object",
          properties: { summary: { type: "string", widget: "missing" } },
        },
        defaultWidgets,
      ),
    ).toThrowError(
      'Unknown form spec widget "missing" for field "summary". Register it in AppRuntime.widgets.',
    );
  });

  test("rejects an unknown Refine relation-filter operator", () => {
    expect(() =>
      deserializeFormSpec(
        {
          type: "object",
          properties: {
            target: {
              type: "string",
              relation: {
                resource: "Channel",
                filters: [
                  {
                    field: "status",
                    operator: "approximately",
                    value: "active",
                  },
                ],
              },
            },
          },
        },
        defaultWidgets,
      ),
    ).toThrowError(
      'Invalid target.relation.filters.0.operator: unknown Refine CRUD operator "approximately".',
    );
  });
});

describe("formSpecInitialValues", () => {
  test("prefills declared fields from payload before schema defaults", () => {
    const fields = deserializeFormSpec(
      {
        type: "object",
        properties: {
          title: { type: "string", defaultValue: "Untitled" },
          approved: { type: "boolean" },
          note: { type: "string", defaultValue: "Review carefully" },
          rows: {
            type: "array",
            items: { type: "object", properties: {} },
          },
        },
      },
      defaultWidgets,
    );

    expect(
      formSpecInitialValues(fields, {
        title: "Import contacts",
        approved: true,
        rows: [{ target: "chn_1" }],
      }),
    ).toEqual({
      title: "Import contacts",
      approved: true,
      note: "Review carefully",
      rows: [{ target: "chn_1" }],
    });
  });
});
