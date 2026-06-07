import { describe, expect, test } from "vitest";

import {
  Action,
  Column,
  Field,
  Group,
  parsePageActions,
  parsePageColumns,
  parsePageFields,
  parsePageGroups,
} from "./index";

interface TestRow {
  title: string;
  updatedAt: string;
}

describe("page element markers", () => {
  test("render null because parent views own rendering", () => {
    expect(Column({ field: "title" })).toBeNull();
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
        <Action id="archive" label="Archive" onClick={archive} danger />
      </Group>,
    );

    expect(groups).toHaveLength(1);
    expect(groups[0]).toMatchObject({
      label: "Details",
      columns: 2,
      fields: [{ name: "tags", widget: "tagInput" }],
      actions: [{ id: "archive", label: "Archive", danger: true }],
    });
    expect(groups[0]?.actions[0]?.onClick).toBe(archive);
  });

  test("parse top-level actions", () => {
    const create = () => undefined;

    expect(
      parsePageActions(
        <>
          <Action id="create" label="New" onClick={create} />
          <Column field="title" />
        </>,
      ),
    ).toEqual([{ id: "create", label: "New", onClick: create }]);
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
      parsePageActions(
        <>
          <Action id="archive" label="Archive" />
          <Action id="archive" label="Archive" />
        </>,
      ),
    ).toThrow("Duplicate page action id: archive");
  });
});
