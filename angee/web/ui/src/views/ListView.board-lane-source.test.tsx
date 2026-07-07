// @vitest-environment happy-dom

import { act, cleanup, render, waitFor } from "@testing-library/react";
import {
  ModelMetadataProvider,
  schemaFieldMetadataFromDataResources,
  type SchemaFieldMetadata,
  type Row,
} from "@angee/metadata";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import type { BoardViewProps } from "./BoardView";
import type { ColumnDescriptor } from "./page";

type LeadRow = Row & {
  id: string;
  name: string;
  stage: { id: string; name: string } | string | null;
};

const harness = vi.hoisted(() => ({
  boardProps: null as BoardViewProps<LeadRow> | null,
  tableRows: [] as LeadRow[],
  laneRows: [] as Row[],
  groupedRows: null as readonly unknown[] | null,
  tableOptions: [] as unknown[],
  useListOptions: [] as unknown[],
  updateOptions: null as unknown,
  updateCalls: [] as unknown[],
  mutateAsync: vi.fn(),
  refetch: vi.fn(),
  toast: {
    danger: vi.fn(),
  },
}));

vi.mock("../i18n", () => ({
  useUiT: () => (key: string) => key,
}));

vi.mock("../feedback", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../feedback")>()),
  useToast: () => harness.toast,
}));

vi.mock("@angee/refine", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/refine")>()),
  useAngeeFacets: () => ({ facets: {} }),
  useOperationDocuments: () => ({}),
}));

vi.mock("@refinedev/core", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@refinedev/core")>();
  return {
    ...actual,
    useList: (options?: unknown) => {
      harness.useListOptions.push(options);
      return {
        result: {
          data: harness.laneRows,
          total: harness.laneRows.length,
        },
        query: {
          error: null,
          isFetching: false,
          refetch: harness.refetch,
        },
      };
    },
    useUpdate: (options?: unknown) => {
      harness.updateOptions = options ?? null;
      return { mutateAsync: harness.mutateAsync };
    },
  };
});

vi.mock("@refinedev/react-table", () => ({
  useTable: (options?: { columns?: readonly unknown[] }) => {
    harness.tableOptions.push(options);
    const rows = harness.tableRows.map(tableRow);
    const groupedRows = harness.groupedRows ?? rows;
    return {
      refineCore: {
        result: {
          data: harness.tableRows,
          total: harness.tableRows.length,
        },
        filters: [],
        setFilters: vi.fn(),
        tableQuery: {
          error: null,
          isFetching: false,
          refetch: harness.refetch,
        },
      },
      reactTable: fakeTable(rows, groupedRows, options?.columns ?? []),
    };
  },
}));

vi.mock("./BoardView", () => ({
  BoardView: (props: BoardViewProps<LeadRow>) => {
    harness.boardProps = props;
    return <div data-testid="board-view" />;
  },
}));

vi.mock("./useBulkDelete", () => ({
  useBulkDelete: () => ({
    canDelete: false,
    deleteInitiate: vi.fn(),
    isPending: false,
    isPreviewOpen: false,
    onCancel: vi.fn(),
    onConfirm: vi.fn(),
    previewBlockedRecordCount: 0,
    previewOverflowCount: 0,
    previewRecordCount: 0,
    previewState: null,
  }),
}));

import { ListView } from "./ListView";

beforeEach(() => {
  harness.boardProps = null;
  harness.tableRows = [
    { id: "led_1", name: "Upgrade", stage: { id: "stg_new", name: "New" } },
  ];
  harness.laneRows = [
    { id: "stg_new", name: "New", code: "NEW" },
    { id: "stg_qualified", name: "Qualified", code: "QUAL" },
    { id: "stg_proposal", name: "Proposal", code: "PROP" },
  ];
  harness.groupedRows = null;
  harness.tableOptions = [];
  harness.useListOptions = [];
  harness.updateOptions = null;
  harness.updateCalls = [];
  harness.mutateAsync.mockReset();
  harness.mutateAsync.mockImplementation(async (variables: unknown) => {
    harness.updateCalls.push(variables);
    return { data: { id: "led_1" } };
  });
  harness.refetch.mockReset();
  harness.toast.danger.mockReset();
});

afterEach(cleanup);

describe("ListView board laneSource", () => {
  test("derives board lanes from the relation source in server order with empty lanes", async () => {
    renderLeadBoard();

    await waitFor(() => {
      expect(harness.boardProps?.groups.map((group) => group.key)).toEqual([
        "stg_new",
        "stg_qualified",
        "stg_proposal",
      ]);
    });
    expect(harness.boardProps?.groups.map((group) => group.label)).toEqual([
      "New",
      "Qualified",
      "Proposal",
    ]);
    expect(rowIdsByLane()).toEqual({
      stg_new: ["led_1"],
      stg_qualified: [],
      stg_proposal: [],
    });
    expect(lastUseListOption()).toMatchObject({
      resource: "crmStages",
      dataProviderName: "console",
      meta: { fields: ["id", "name"] },
    });
  });

  test("pins the board group axis from laneSource without a default group", async () => {
    renderLeadBoard({ withDefaultGroup: false });

    await waitFor(() => {
      expect(harness.boardProps?.resourceView.state.groupStack).toEqual([
        { field: "stage" },
      ]);
    });
  });

  test("fails fast when laneSource does not resolve a relation", () => {
    expect(() => renderLeadBoard({ laneSource: { field: "name" } })).toThrow(
      /laneSource field "name".*relation/,
    );
  });

  test("selects the lane relation id on the row list wire", async () => {
    renderLeadBoard({
      columns: [
        { field: "name", header: "Name" },
        { field: "customer", header: "Customer" },
      ],
    });

    await waitFor(() => expect(harness.boardProps).not.toBeNull());

    expect(lastTableOption()).toMatchObject({
      refineCoreProps: {
        meta: {
          fields: expect.arrayContaining([
            "id",
            "name",
            { customer: ["name"] },
            { stage: ["id"] },
          ]),
        },
      },
    });
  });

  test("uses the declared lane label field for the lane option wire", async () => {
    renderLeadBoard({ laneSource: { field: "stage", labelField: "code" } });

    await waitFor(() => {
      expect(harness.boardProps?.groups.map((group) => group.label)).toEqual([
        "NEW",
        "QUAL",
        "PROP",
      ]);
    });
    expect(lastUseListOption()).toMatchObject({
      meta: { fields: ["id", "code"] },
    });
  });

  test("keeps the derived board lanes when no laneSource is declared", async () => {
    harness.groupedRows = [
      groupedTableRow("stage:New", "New", [harness.tableRows[0]!]),
    ];

    renderLeadBoard({ laneSource: undefined });

    await waitFor(() => {
      expect(harness.boardProps?.groups.map((group) => group.key)).toEqual([
        "stage:New",
      ]);
    });
    expect(harness.boardProps?.groups.map((group) => group.label)).toEqual([
      "New",
    ]);
    expect(lastUseListOption()).toMatchObject({
      resource: "__angee_disabled__",
      queryOptions: { enabled: false },
    });
  });

  test("enables drag only when a laneSource is declared and the group field is writable", async () => {
    renderLeadBoard({ laneSource: undefined });
    await waitFor(() => expect(harness.boardProps).not.toBeNull());
    expect(harness.boardProps?.dragEnabled).toBe(false);
    expect(harness.boardProps?.onCardMove).toBeUndefined();

    cleanup();
    renderLeadBoard({ metadata: leadMetadata({ stageWritable: false }) });
    await waitFor(() => expect(harness.boardProps).not.toBeNull());
    expect(harness.boardProps?.dragEnabled).toBe(false);
    expect(harness.boardProps?.onCardMove).toBeUndefined();
  });

  test("suppresses refine default notifications for lane restage writes", async () => {
    renderLeadBoard();

    await waitFor(() => expect(harness.boardProps).not.toBeNull());

    expect(harness.updateOptions).toMatchObject({
      resource: "crmLeads",
      successNotification: false,
      errorNotification: false,
    });
  });

  test("disables dropping on the empty lane when the lane field is not nullable", async () => {
    harness.tableRows = [
      { id: "led_1", name: "Upgrade", stage: null },
    ];

    renderLeadBoard({ metadata: leadMetadata({ stageNullable: false }) });

    await waitFor(() => {
      const emptyLane = harness.boardProps?.groups.find((group) => group.key === "");
      expect(emptyLane).toMatchObject({ dropDisabled: true });
    });
  });

  test("labels undeclared lanes from folded relation rows before the i18n fallback", async () => {
    harness.laneRows = [{ id: "stg_new", name: "New", code: "NEW" }];
    harness.tableRows = [
      { id: "led_1", name: "Upgrade", stage: { id: "stg_hidden", name: "Hidden" } },
    ];

    renderLeadBoard();

    await waitFor(() => {
      expect(
        harness.boardProps?.groups.find((group) => group.key === "stg_hidden")?.label,
      ).toBe("Hidden");
    });

    cleanup();
    harness.boardProps = null;
    harness.tableRows = [
      { id: "led_1", name: "Upgrade", stage: "stg_hidden" },
    ];

    renderLeadBoard();

    await waitFor(() => {
      expect(
        harness.boardProps?.groups.find((group) => group.key === "stg_hidden")?.label,
      ).toBe("list.unknownValue");
    });
  });

  test("optimistically moves a card, writes the group field, and reverts with a toast on error", async () => {
    let rejectMove: (error: Error) => void = () => undefined;
    harness.mutateAsync.mockImplementation((variables: unknown) => {
      harness.updateCalls.push(variables);
      return new Promise((_resolve, reject) => {
        rejectMove = reject;
      });
    });

    renderLeadBoard();
    await waitFor(() => expect(rowIdsByLane().stg_new).toEqual(["led_1"]));

    let move: Promise<void> | undefined;
    act(() => {
      move = harness.boardProps?.onCardMove?.(
        harness.tableRows[0]!,
        "stg_qualified",
      ) as Promise<void> | undefined;
    });

    expect(harness.updateCalls).toEqual([
      { id: "led_1", values: { stage: "stg_qualified" } },
    ]);
    await waitFor(() => {
      expect(rowIdsByLane().stg_qualified).toEqual(["led_1"]);
    });

    await act(async () => {
      rejectMove(new Error("write rejected"));
      await move;
    });

    await waitFor(() => {
      expect(rowIdsByLane().stg_new).toEqual(["led_1"]);
    });
    expect(harness.toast.danger).toHaveBeenCalledWith({
      title: "board.moveFailed",
      description: "write rejected",
    });
  });
});

function renderLeadBoard(options: {
  laneSource?: { field: string; labelField?: string } | undefined;
  metadata?: SchemaFieldMetadata;
  columns?: readonly ColumnDescriptor<LeadRow>[];
  withDefaultGroup?: boolean;
} = {}) {
  const laneSource = Object.prototype.hasOwnProperty.call(options, "laneSource")
    ? options.laneSource
    : { field: "stage" };
  const metadata = options.metadata ?? leadMetadata();
  return render(
    <ModelMetadataProvider metadata={metadata}>
      <ListView<LeadRow>
        resource="crm.Lead"
        columns={options.columns ?? COLUMNS}
        defaultView="board"
        defaultGroup={options.withDefaultGroup === false ? undefined : { field: "stage" }}
        laneSource={laneSource}
        scope="local"
      />
    </ModelMetadataProvider>,
  );
}

function rowIdsByLane(): Record<string, string[]> {
  return Object.fromEntries(
    (harness.boardProps?.groups ?? []).map((group) => [
      group.key,
      group.rows.map((row) => row.id),
    ]),
  );
}

function lastUseListOption(): {
  resource?: string;
  dataProviderName?: string;
  meta?: { fields?: unknown };
} | undefined {
  return harness.useListOptions.at(-1) as
    | { resource?: string; dataProviderName?: string; meta?: { fields?: unknown } }
    | undefined;
}

function lastTableOption(): {
  refineCoreProps?: { meta?: { fields?: unknown } };
} | undefined {
  return harness.tableOptions.at(-1) as
    | { refineCoreProps?: { meta?: { fields?: unknown } } }
    | undefined;
}

function tableRow(row: LeadRow) {
  return {
    id: row.id,
    original: row,
    depth: 0,
    subRows: [],
    getIsGrouped: () => false,
  };
}

function groupedTableRow(id: string, label: string, rows: readonly LeadRow[]) {
  return {
    id,
    depth: 0,
    groupingColumnId: "stage",
    getGroupingValue: () => label,
    getIsGrouped: () => true,
    original: rows[0],
    subRows: rows.map(tableRow),
  };
}

function fakeTable(
  rows: readonly unknown[],
  groupedRows: readonly unknown[],
  columns: readonly unknown[],
) {
  const leafColumns = columns.map((column) => {
    const def = column as { id?: string; meta?: unknown };
    return {
      id: def.id ?? "column",
      columnDef: column,
      getIsVisible: () => true,
      toggleVisibility: vi.fn(),
    };
  });
  return {
    options: { columns },
    getAllLeafColumns: () => leafColumns,
    getVisibleLeafColumns: () => leafColumns,
    getRowModel: () => ({ rows }),
    getGroupedRowModel: () => ({ rows: groupedRows }),
    getState: () => ({ rowSelection: {} }),
    getIsAllPageRowsSelected: () => false,
    getIsSomePageRowsSelected: () => false,
    toggleAllPageRowsSelected: vi.fn(),
  };
}

const COLUMNS = [
  { field: "name", header: "Name" },
  { field: "stage", header: "Stage" },
] as ColumnDescriptor<LeadRow>[];

function leadMetadata(
  {
    stageNullable = true,
    stageWritable = true,
  }: {
    stageNullable?: boolean;
    stageWritable?: boolean;
  } = {},
): SchemaFieldMetadata {
  return schemaFieldMetadataFromDataResources([
    {
      schemaName: "console",
      modelLabel: "crm.Lead",
      appLabel: "crm",
      modelName: "lead",
      publicIdField: "sqid",
      roots: { list: "crmLeads", update: "updateCrmLead" },
      typeNames: { node: "LeadType" },
      recordRepresentation: "name",
      capabilities: ["list", "update"],
      fields: [
        field("id", { scalar: "ID", updatable: false }),
        field("name", { scalar: "String" }),
        field("stage", {
          kind: "relation",
          relationModelLabel: "crm.Stage",
          relationObject: true,
          groupable: true,
          nullable: stageNullable,
          updatable: stageWritable,
        }),
        field("customer", {
          kind: "relation",
          relationModelLabel: "crm.Customer",
          relationObject: true,
        }),
      ],
      filterFields: ["id", "name", "stage"],
      orderFields: ["name"],
      aggregateFields: ["id"],
      groupByFields: ["stage"],
      updateFields: stageWritable ? ["name", "stage"] : ["name"],
      groupDimensions: [{ field: "stage", input: "stage", key: "stage", kind: "relation" }],
      relationAxes: [
        {
          field: "stage",
          modelLabel: "crm.Stage",
          publicIdField: "sqid",
          labelAxis: "stage__name",
        },
      ],
    },
    {
      schemaName: "console",
      modelLabel: "crm.Stage",
      appLabel: "crm",
      modelName: "stage",
      publicIdField: "sqid",
      roots: { list: "crmStages" },
      typeNames: { node: "StageType" },
      recordRepresentation: "name",
      capabilities: ["list"],
      fields: [
        field("id", { scalar: "ID", updatable: false }),
        field("name", { scalar: "String" }),
        field("code", { scalar: "String" }),
      ],
      filterFields: ["id", "name", "code"],
      orderFields: ["position", "id"],
      aggregateFields: ["id"],
      groupByFields: [],
      relationAxes: [],
    },
    {
      schemaName: "console",
      modelLabel: "crm.Customer",
      appLabel: "crm",
      modelName: "customer",
      publicIdField: "sqid",
      roots: { list: "crmCustomers" },
      typeNames: { node: "CustomerType" },
      recordRepresentation: "name",
      capabilities: ["list"],
      fields: [
        field("id", { scalar: "ID", updatable: false }),
        field("name", { scalar: "String" }),
      ],
      filterFields: ["id", "name"],
      orderFields: ["name"],
      aggregateFields: ["id"],
      groupByFields: [],
      relationAxes: [],
    },
  ]);
}

function field(
  name: string,
  overrides: Partial<{
    kind: "scalar" | "relation";
    scalar: string;
    relationModelLabel: string;
    relationObject: boolean;
    groupable: boolean;
    nullable: boolean;
    updatable: boolean;
  }> = {},
) {
  const kind = overrides.kind ?? "scalar";
  return {
    name,
    kind,
    ...(overrides.scalar ? { scalar: overrides.scalar } : {}),
    ...(overrides.relationModelLabel
      ? { relationModelLabel: overrides.relationModelLabel }
      : {}),
    ...(overrides.relationObject !== undefined
      ? { relationObject: overrides.relationObject }
      : {}),
    readable: true,
    filterable: true,
    sortable: false,
    aggregatable: name === "id",
    groupable: overrides.groupable ?? false,
    nullable: overrides.nullable ?? false,
    creatable: true,
    updatable: overrides.updatable ?? true,
    requiredOnCreate: false,
  };
}
