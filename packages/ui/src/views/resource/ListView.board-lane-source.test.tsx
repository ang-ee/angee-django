// @vitest-environment happy-dom

import { act, cleanup, render, waitFor } from "@testing-library/react";
import {
  ModelMetadataProvider,
  ResourceQuery,
  schemaFieldMetadataFromDataResources,
  type SchemaFieldMetadata,
  type Row,
} from "@angee/metadata";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import type { BoardViewProps } from "./BoardView";
import type { ColumnDescriptor } from "../page";
import { defineRowAction, type RowActionDeclaration } from "./RowActions";
import type { BoardLaneSource, CardActionContext } from "./resource-view-types";

type LeadRow = Row & {
  id: string;
  name: string;
  stage: { id: string; name: string } | string | null;
  sort_order?: number;
};

const harness = vi.hoisted(() => ({
  boardProps: null as BoardViewProps<LeadRow> | null,
  tableRows: [] as LeadRow[],
  laneRows: [] as Row[],
  useListOptions: [] as unknown[],
  updateOptions: null as unknown,
  updateCalls: [] as unknown[],
  mutateAsync: vi.fn(),
  refetch: vi.fn(),
  toast: {
    danger: vi.fn(),
  },
}));

vi.mock("../../i18n", () => ({
  useUiT: () => (key: string) => key,
}));

vi.mock("../../feedback", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../feedback")>()),
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
    useList: (options?: { resource?: string }) => {
      harness.useListOptions.push(options);
      const rows = options?.resource === "crmLeads" ? harness.tableRows : harness.laneRows;
      return {
        result: {
          data: rows,
          total: rows.length,
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
        {
          field: "stage",
        },
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
      meta: {
        fields: expect.arrayContaining([
          "id", "name", { customer: ["id", "name"] }, { stage: ["id", "name"] },
        ]),
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

  test("forwards declared filters and sorters to the lane relation query", async () => {
    const filters = [
      { field: "pipeline", operator: "eq" as const, value: "pip_sales" },
    ];
    const sorters = [{ field: "position", order: "asc" as const }];
    renderLeadBoard({ laneSource: { field: "stage", filters, sorters } });

    await waitFor(() => expect(harness.boardProps).not.toBeNull());

    expect(lastUseListOption()).toMatchObject({ filters, sorters });
  });

  test("orders by a declared rank and persists lane plus midpoint request", async () => {
    harness.tableRows = [
      {
        id: "led_2",
        name: "Second",
        stage: { id: "stg_new", name: "New" },
        sort_order: 2048,
      },
      {
        id: "led_1",
        name: "First",
        stage: { id: "stg_new", name: "New" },
        sort_order: 1024,
      },
    ];
    renderLeadBoard({
      laneSource: { field: "stage", rankField: "sort_order" },
    });

    await waitFor(() => {
      expect(rowIdsByLane().stg_new).toEqual(["led_1", "led_2"]);
    });
    expect(harness.boardProps?.rankField).toBe("sort_order");
    expect(lastTableOption()).toMatchObject({
      meta: { fields: expect.arrayContaining(["sort_order"]) },
    });

    await act(async () => {
      await harness.boardProps?.onCardMove?.(
        harness.tableRows[0]!,
        "stg_qualified",
        1536,
      );
    });
    expect(harness.updateCalls).toEqual([
      {
        id: "led_2",
        values: { stage: "stg_qualified", sort_order: 1536 },
      },
    ]);
  });

  test("reads a declared lane fold fact as the initial collapsed state", async () => {
    harness.laneRows = [
      { id: "stg_new", name: "New", fold: true },
      { id: "stg_qualified", name: "Qualified", fold: false },
    ];
    renderLeadBoard({
      laneSource: { field: "stage", foldField: "fold" },
    });

    await waitFor(() => {
      expect(
        harness.boardProps?.groups.find((group) => group.key === "stg_new"),
      ).toMatchObject({ defaultCollapsed: true });
    });
    expect(lastUseListOption()).toMatchObject({
      meta: { fields: ["id", "name", "fold"] },
    });
  });

  test("keeps native client grouping when no laneSource is declared for a bounded resource", async () => {
    harness.tableRows = [{ id: "led_1", name: "Upgrade", stage: { id: "New", name: "New" } }];
    renderLeadBoard({ laneSource: undefined, metadata: leadMetadata({ rowModel: "client" }) });

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
    renderLeadBoard({ laneSource: undefined, metadata: leadMetadata({ rowModel: "client" }) });
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

  test("does not synthesize a board footer when page and declared actions are empty", async () => {
    const hidden = defineRowAction<LeadRow>({
      kind: "page",
      id: "hidden",
      label: "Hidden",
      variant: "ghost",
      visible: () => false,
      pendingPolicy: "disable-actions",
      onSelect: () => undefined,
    });
    renderLeadBoard({
      rowActions: [hidden],
      cardActions: () => null,
    });

    await waitFor(() => expect(harness.boardProps).not.toBeNull());
    expect(
      harness.boardProps?.cardActions?.(
        harness.tableRows[0]!,
        { refresh: vi.fn() },
      ),
    ).toBeNull();
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
    expect(harness.refetch).toHaveBeenCalledTimes(1);
  });
});

function renderLeadBoard(options: {
  laneSource?: BoardLaneSource | undefined;
  metadata?: SchemaFieldMetadata;
  columns?: readonly ColumnDescriptor<LeadRow>[];
  withDefaultGroup?: boolean;
  rowActions?: readonly RowActionDeclaration<LeadRow>[];
  cardActions?: (row: LeadRow, context: CardActionContext) => ReactNode;
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
        rowActions={options.rowActions}
        cardActions={options.cardActions}
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
  filters?: readonly unknown[];
  sorters?: readonly unknown[];
  meta?: { fields?: unknown };
} | undefined {
  return harness.useListOptions.at(-1) as
    | {
        resource?: string;
        dataProviderName?: string;
        filters?: readonly unknown[];
        sorters?: readonly unknown[];
        meta?: { fields?: unknown };
      }
    | undefined;
}

function lastTableOption() {
  return harness.useListOptions.findLast((options) =>
    (options as { resource?: string }).resource === "crmLeads",
  );
}

const COLUMNS = [
  { field: "name", header: "Name" },
  { field: "stage", header: "Stage" },
] as ColumnDescriptor<LeadRow>[];

function leadMetadata(
  {
    stageNullable = true,
    stageWritable = true,
    rowModel = "server",
  }: {
    stageNullable?: boolean;
    stageWritable?: boolean;
    rowModel?: "client" | "server";
  } = {},
): SchemaFieldMetadata {
  const leadQuery = ResourceQuery.forRows({ fields: {
    id: { scalar: "ID" }, name: { scalar: "String" }, sort_order: { scalar: "Float" },
    stage: { kind: "relation", identityPath: "stage.id", labelPath: "stage.name" },
    customer: { kind: "relation", identityPath: "customer.id", labelPath: "customer.name" },
  } }).contract;
  leadQuery.fields.stage!.relation = { model: "crm.Stage", identityPath: "stage.id", labelPath: "stage.name" };
  leadQuery.fields.customer!.relation = { model: "crm.Customer", identityPath: "customer.id", labelPath: "customer.name" };
  leadQuery.axes.stage!.server = { input: "stage", key: "stage" };
  return schemaFieldMetadataFromDataResources([
    {
      schemaName: "console",
      modelLabel: "crm.Lead",
      appLabel: "crm",
      modelName: "lead",
      rowModel,
      roots: { list: "crmLeads", aggregate: "crmLeads_aggregate", update: "updateCrmLead" },
      typeNames: { node: "LeadType", filter: "LeadBoolExp", order: "LeadOrderBy" },
      recordRepresentation: "name",
      capabilities: ["list", "update"],
      fields: [
        field("id", { scalar: "ID", updatable: false }),
        field("name", { scalar: "String" }),
        field("sort_order", { scalar: "Float" }),
        field("stage", {
          kind: "relation",
          relationModelLabel: "crm.Stage",
          relationObject: true,
          nullable: stageNullable,
          updatable: stageWritable,
        }),
        field("customer", {
          kind: "relation",
          relationModelLabel: "crm.Customer",
          relationObject: true,
        }),
      ],
      query: leadQuery,
      aggregateFields: ["id"],
      updateFields: stageWritable ? ["name", "stage", "sort_order"] : ["name", "sort_order"],
    },
    {
      schemaName: "console",
      modelLabel: "crm.Stage",
      appLabel: "crm",
      modelName: "stage",
      roots: { list: "crmStages" },
      typeNames: { node: "StageType" },
      recordRepresentation: "name",
      capabilities: ["list"],
      fields: [
        field("id", { scalar: "ID", updatable: false }),
        field("name", { scalar: "String" }),
        field("code", { scalar: "String" }),
        field("fold", { scalar: "Boolean", updatable: false }),
      ],
      query: ResourceQuery.forRows({ fields: { id: { scalar: "ID" }, name: { scalar: "String" }, code: { scalar: "String" }, fold: { scalar: "Boolean" }, position: { scalar: "Int" }, pipeline: { scalar: "ID" } } }).contract,
      aggregateFields: ["id"],
    },
    {
      schemaName: "console",
      modelLabel: "crm.Customer",
      appLabel: "crm",
      modelName: "customer",
      roots: { list: "crmCustomers" },
      typeNames: { node: "CustomerType" },
      recordRepresentation: "name",
      capabilities: ["list"],
      fields: [
        field("id", { scalar: "ID", updatable: false }),
        field("name", { scalar: "String" }),
      ],
      query: ResourceQuery.forRows({ fields: { id: { scalar: "ID" }, name: { scalar: "String" } } }).contract,
      aggregateFields: ["id"],
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
    aggregatable: name === "id",
    nullable: overrides.nullable ?? false,
    creatable: true,
    updatable: overrides.updatable ?? true,
    requiredOnCreate: false,
  };
}
