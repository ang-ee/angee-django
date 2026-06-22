import { describe, expect, test } from "vitest";

import {
  Action,
  Column,
  columnTone,
  Facet,
  Field,
  fieldWidgetId,
  Group,
  isRelationIdField,
  parsePageActions,
  parsePageColumns,
  parsePageFacets,
  parsePageFields,
  parsePageGroups,
} from "./index";
import type { ColumnDescriptor } from "./Column";

interface TestRow {
  title: string;
  updatedAt: string;
}

describe("columnTone", () => {
  const toned: ColumnDescriptor = {
    field: "status",
    tone: { active: "success", blocked: "danger" },
  };

  test("resolves a value against the column's tone map", () => {
    expect(columnTone(toned, "active")).toBe("success");
    expect(columnTone(toned, "blocked")).toBe("danger");
  });

  test("falls back to neutral for an unmapped or nullish value", () => {
    expect(columnTone(toned, "unknown")).toBe("neutral");
    expect(columnTone(toned, null)).toBe("neutral");
    expect(columnTone(toned, undefined)).toBe("neutral");
  });

  test("returns undefined when the column declares no tone map", () => {
    expect(columnTone({ field: "title" }, "anything")).toBeUndefined();
  });
});

describe("page element markers", () => {
  test("render null because parent views own rendering", () => {
    expect(Column({ field: "title" })).toBeNull();
    expect(Facet({ field: "provider" })).toBeNull();
    expect(Field({ name: "title" })).toBeNull();
    expect(Group({ label: "Details" })).toBeNull();
    expect(Action({ id: "delete", label: "Delete" })).toBeNull();
  });

  test("parse column markers and ignore unrelated children", () => {
    const renderTitle = (row: TestRow) => row.title.toUpperCase();

    const columns = parsePageColumns<TestRow>(
      <>
        <Column<TestRow>
          field="title"
          header="Title"
          widget="text"
          sortable
          aggregate="count"
          align="left"
          render={renderTitle}
        />
        <span>ignored</span>
        <Column field="updatedAt" header="Updated" align="right" />
      </>,
    );

    expect(columns).toHaveLength(2);
    expect(columns[0]).toMatchObject({
      field: "title",
      header: "Title",
      widget: "text",
      sortable: true,
      aggregate: "count",
      align: "left",
    });
    expect(columns[0]?.render).toBe(renderTitle);
    expect(columns[1]).toMatchObject({
      field: "updatedAt",
      header: "Updated",
      align: "right",
    });
  });

  test("parse relation facet markers", () => {
    const facets = parsePageFacets(
      <>
        <Facet field="provider" label="Provider" labelField="name" />
        <Column field="title" />
      </>,
    );

    expect(facets).toEqual([
      {
        field: "provider",
        label: "Provider",
        labelField: "name",
      },
    ]);
  });

  test("parse fields recursively through groups", () => {
    const fields = parsePageFields(
      <>
        <Field name="title" label="Title" widget="text" kind="text" />
        <Group label="Details" columns={2}>
          <Field
            name="state"
            label="Status"
            widget="statusbar"
            readOnly
            title
            kind="selection"
          />
        </Group>
      </>,
    );

    expect(fields).toEqual([
      {
        name: "title",
        label: "Title",
        widget: "text",
        kind: "text",
      },
      {
        name: "state",
        label: "Status",
        widget: "statusbar",
        readOnly: true,
        title: true,
        kind: "selection",
      },
    ]);
  });

  test("parse group fields and group actions", () => {
    const archive = () => undefined;

    const groups = parsePageGroups(
      <Group label="Details" columns={2}>
        <Field name="tags" widget="tagInput" />
        <Action id="archive" label="Archive" run={archive} danger />
      </Group>,
    );

    expect(groups).toHaveLength(1);
    expect(groups[0]).toMatchObject({
      label: "Details",
      columns: 2,
      fields: [{ name: "tags", widget: "tagInput" }],
      actions: [{ id: "archive", label: "Archive", danger: true }],
    });
    expect(groups[0]?.actions[0]?.run).toBe(archive);
  });

  test("parse top-level actions", () => {
    const create = () => undefined;

    expect(
      parsePageActions(
        <>
          <Action id="create" label="New" run={create} />
          <Column field="title" />
        </>,
      ),
    ).toEqual([{ id: "create", label: "New", run: create }]);
  });

  test("preserve parsed descriptor identity for stable element constants", () => {
    const listDeclaration = (
      <>
        <Column field="title" />
        <Column field="updatedAt" />
      </>
    );
    const firstColumns = parsePageColumns(listDeclaration);
    const secondColumns = parsePageColumns(listDeclaration);

    expect(secondColumns).toBe(firstColumns);
    expect(secondColumns[0]).toBe(firstColumns[0]);

    const formDeclaration = (
      <Group label="Details">
        <Field name="title" />
      </Group>
    );
    const firstGroups = parsePageGroups(formDeclaration);
    const secondGroups = parsePageGroups(formDeclaration);

    expect(secondGroups).toBe(firstGroups);
    expect(secondGroups[0]).toBe(firstGroups[0]);
    expect(secondGroups[0]?.fields[0]).toBe(firstGroups[0]?.fields[0]);
  });

  test("fail fast on duplicate descriptor owners", () => {
    expect(() =>
      parsePageColumns(
        <>
          <Column field="title" />
          <Column field="title" />
        </>,
      ),
    ).toThrow("Duplicate page column field: title");

    expect(() =>
      parsePageFields(
        <>
          <Field name="title" />
          <Group>
            <Field name="title" />
          </Group>
        </>,
      ),
    ).toThrow("Duplicate page field name: title");

    expect(() =>
      parsePageFacets(
        <>
          <Facet field="provider" />
          <Facet field="provider" />
        </>,
      ),
    ).toThrow("Duplicate page facet field: provider");

    expect(() =>
      parsePageActions(
        <>
          <Action id="archive" label="Archive" />
          <Action id="archive" label="Archive" />
        </>,
      ),
    ).toThrow("Duplicate page action id: archive");
  });
});

describe("field descriptor resolution", () => {
  test("fieldWidgetId prefers widget, then kind, then text", () => {
    expect(fieldWidgetId({ name: "a", widget: "select", kind: "text" })).toBe(
      "select",
    );
    expect(fieldWidgetId({ name: "a", kind: "switch" })).toBe("switch");
    expect(fieldWidgetId({ name: "a" })).toBe("text");
    // An empty widget string falls through to kind (truthy, not nullish).
    expect(fieldWidgetId({ name: "a", widget: "", kind: "switch" })).toBe(
      "switch",
    );
    expect(fieldWidgetId({ name: "a", widget: "" })).toBe("text");
  });

  test("isRelationIdField is true only for the many2one widget", () => {
    expect(isRelationIdField({ name: "a", widget: "many2one" })).toBe(true);
    expect(isRelationIdField({ name: "a", kind: "many2one" })).toBe(true);
    expect(isRelationIdField({ name: "a", widget: "select" })).toBe(false);
    expect(isRelationIdField({ name: "a" })).toBe(false);
  });
});
