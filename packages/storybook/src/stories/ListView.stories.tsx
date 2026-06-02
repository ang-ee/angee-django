import { useMemo, useState } from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";
import type { UseResourceListResult } from "@angee/sdk";
import {
  Badge,
  Checkbox,
  Chip,
  DataToolbar,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@angee/base";

const rows = [
  {
    id: "note-1",
    title: "Permission model notes",
    tags: ["architecture", "iam"],
    status: "Active",
    owner: "Alexis",
    words: 1840,
    updated: "2 hours ago",
  },
  {
    id: "note-2",
    title: "CSV import edge cases",
    tags: ["resources"],
    status: "Draft",
    owner: "Sofia",
    words: 920,
    updated: "Yesterday",
  },
  {
    id: "note-3",
    title: "Workspace lifecycle",
    tags: ["composer", "dev"],
    status: "Archived",
    owner: "Mina",
    words: 2460,
    updated: "May 28",
  },
] as const;

type ColumnId = "title" | "tags" | "status" | "owner" | "words" | "updated";
type ColumnVisibility = Record<ColumnId, boolean>;

const columns = [
  { id: "title", label: "Title" },
  { id: "tags", label: "Tags" },
  { id: "status", label: "Status" },
  { id: "owner", label: "Owner" },
  { id: "words", label: "Words", align: "right" },
  { id: "updated", label: "Updated" },
] satisfies readonly { id: ColumnId; label: string; align?: "right" }[];

const list = {
  rows,
  total: rows.length,
  pageCount: 1,
  page: 1,
  pageSize: 50,
  pageInfo: undefined,
  hasNext: false,
  hasPrev: false,
  setPage: () => undefined,
  firstPage: () => undefined,
  nextPage: () => undefined,
  prevPage: () => undefined,
  lastPage: () => undefined,
  fetching: false,
  error: null,
  refetch: () => undefined,
} satisfies UseResourceListResult;

const meta = {
  title: "Views/ListView",
  parameters: { layout: "padded" },
} satisfies Meta;

export default meta;

type Story = StoryObj<typeof meta>;

export const VisibleFieldsChooser: Story = {
  render: () => <ListFixture />,
};

function ListFixture() {
  const [columnVisibility, setColumnVisibility] = useState<ColumnVisibility>(
    () => Object.fromEntries(
      columns.map((column) => [column.id, true]),
    ) as ColumnVisibility,
  );
  const visibleColumns = useMemo(
    () => columns.filter((column) => columnVisibility[column.id]),
    [columnVisibility],
  );
  const visibleFields = columns.map((column) => ({
    id: column.id,
    label: column.label,
    visible: columnVisibility[column.id],
    disabled: columnVisibility[column.id] && visibleColumns.length <= 1,
  }));

  return (
    <div className="max-w-5xl overflow-hidden rounded-md border border-border bg-sheet">
      <DataToolbar
        pager={{
          total: list.total,
          page: list.page,
          pageSize: list.pageSize,
          hasNext: list.hasNext,
          hasPrev: list.hasPrev,
        }}
        view="list"
        onViewChange={() => undefined}
        visibleFields={visibleFields}
        createLabel="New note"
        onCreate={() => undefined}
        onVisibleFieldToggle={(id, visible) => {
          if (!isColumnId(id)) return;
          setColumnVisibility((current) => {
            if (!visible && current[id] && visibleColumns.length <= 1) {
              return current;
            }
            return { ...current, [id]: visible };
          });
        }}
      />
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead sticky className="w-8">
              <Checkbox size="sm" aria-label="Select all rows" />
            </TableHead>
            {visibleColumns.map((column) => (
              <TableHead
                key={column.id}
                sticky
                className={column.align === "right" ? "text-right" : undefined}
              >
                {column.label}
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row) => (
            <TableRow key={row.id} interactive>
              <TableCell className="w-8">
                <Checkbox size="sm" aria-label={`Select ${row.title}`} />
              </TableCell>
              {visibleColumns.map((column) => (
                <TableCell
                  key={column.id}
                  className={column.align === "right" ? "text-right" : undefined}
                >
                  {renderCell(row, column.id)}
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

function renderCell(
  row: (typeof rows)[number],
  column: ColumnId,
) {
  if (column === "title") {
    return <span className="font-medium text-fg">{row.title}</span>;
  }
  if (column === "tags") {
    return (
      <span className="inline-flex min-w-0 flex-wrap gap-1">
        {row.tags.map((tag) => (
          <Chip key={tag} tone="info" size="sm">
            {tag}
          </Chip>
        ))}
      </span>
    );
  }
  if (column === "status") {
    return <Badge variant={statusVariant(row.status)}>{row.status}</Badge>;
  }
  if (column === "words") {
    return row.words.toLocaleString();
  }
  return row[column];
}

function isColumnId(value: string): value is ColumnId {
  return columns.some((column) => column.id === value);
}

function statusVariant(
  status: (typeof rows)[number]["status"],
): "success" | "warning" | "default" {
  if (status === "Active") return "success";
  if (status === "Draft") return "warning";
  return "default";
}
