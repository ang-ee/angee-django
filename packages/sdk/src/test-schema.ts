import type { AngeeSchemaMetadata } from "./model-metadata";

export const TEST_SCHEMA_SDL = /* GraphQL */ `
  directive @oneOf on INPUT_OBJECT

  scalar BigInt
  scalar DateTime
  scalar JSON

  interface Node {
    id: ID!
  }

  type OffsetPaginationInfo {
    offset: Int!
    limit: Int
  }

  input OffsetPaginationInput {
    offset: Int! = 0
    limit: Int
  }

  enum Ordering {
    ASC
    DESC
  }

  enum OrderDirection {
    ASC
    DESC
  }

  enum SaleState {
    DRAFT
    OPEN
    CLOSED
  }

  type Sale implements Node {
    id: ID!
    title: String!
    state: SaleState!
    amount: Int!
    createdAt: DateTime!
  }

  type SaleOffsetPaginated {
    pageInfo: OffsetPaginationInfo!
    totalCount: Int!
    results: [Sale!]!
  }

  input StrFilterLookup {
    iContains: String
  }

  input SaleStateFilterLookup {
    exact: SaleState
  }

  input SaleFilter {
    title: StrFilterLookup
    state: SaleStateFilterLookup
  }

  input SaleOrder @oneOf {
    title: Ordering
    state: Ordering
  }

  input SaleInput {
    title: String!
  }

  input SalePatch {
    id: ID!
    title: String
  }

  enum SaleGroupableField {
    STATE
    CREATED_AT
  }

  input SaleGroupBySpec {
    field: SaleGroupableField!
  }

  input SaleGroupOrder {
    field: String!
    direction: OrderDirection! = ASC
  }

  type SaleGroupKey {
    state: SaleState
    createdAtMonth: DateTime
  }

  type SaleGrouped {
    key: SaleGroupKey!
    count: Int!
    filter: JSON!
    sum: SaleSumFields
  }

  type SaleGroupedResult {
    pageInfo: OffsetPaginationInfo!
    totalCount: Int!
    results: [SaleGrouped!]!
  }

  type SaleAggregate {
    count: Int!
    sum: SaleSumFields
  }

  type SaleRevision {
    id: ID!
    createdAt: DateTime!
    comment: String
    title: String!
  }

  type SaleSumFields {
    amount: BigInt
  }

  type DeletePreviewGroup {
    label: String!
    count: Int!
  }

  type DeletePreviewNode {
    label: String!
    objectLabel: String!
    objectId: String
    children: [DeletePreviewNode!]!
  }

  type DeletePreview {
    totalDeletedCount: Int!
    deleted: [DeletePreviewGroup!]!
    updated: [DeletePreviewGroup!]!
    blocked: [DeletePreviewGroup!]!
    hasBlockers: Boolean!
    root: DeletePreviewNode!
  }

  type Query {
    sale(id: ID!): Sale
    sales(
      pagination: OffsetPaginationInput
      filters: SaleFilter
      order: SaleOrder
    ): SaleOffsetPaginated!
    saleAggregate(filter: SaleFilter): SaleAggregate!
    saleGroups(
      groupBy: [SaleGroupBySpec!]!
      pagination: OffsetPaginationInput
      filter: SaleFilter
      orderBy: [SaleGroupOrder!] = null
    ): SaleGroupedResult!
    saleRevisions(id: ID!): [SaleRevision!]!
  }

  type Mutation {
    createSale(data: SaleInput!): Sale!
    updateSale(data: SalePatch!): Sale!
    deleteSale(id: ID!, confirm: Boolean! = false): DeletePreview!
  }
`;

export const TEST_SCHEMA_METADATA: AngeeSchemaMetadata = {
  angee: {
    resources: [
      {
        schemaName: "public",
        modelLabel: "Sale",
        appLabel: "",
        modelName: "sale",
        publicIdField: "sqid",
        roots: {
          detail: "sale",
          list: "sales",
          aggregate: "saleAggregate",
          groups: "saleGroups",
          revisions: "saleRevisions",
          create: "createSale",
          update: "updateSale",
          deletePreview: "deleteSale",
        },
        typeNames: {
          node: "Sale",
          filter: "SaleFilter",
          order: "SaleOrder",
          aggregate: "SaleAggregate",
          groupKey: "SaleGroupKey",
          groupBySpec: "SaleGroupBySpec",
          groupOrder: "SaleGroupOrder",
          createInput: "SaleInput",
          updateInput: "SalePatch",
          deletePayload: "DeletePreview",
          revision: "SaleRevision",
        },
        capabilities: [
          "list",
          "detail",
          "aggregate",
          "groups",
          "revisions",
          "create",
          "update",
          "deletePreview",
        ],
        filterFields: ["title", "state"],
        orderFields: ["title", "state"],
        aggregateFields: ["id", "amount"],
        groupByFields: ["state", "createdAt"],
        createFields: ["title"],
        updateFields: ["title"],
        requiredCreateFields: ["title"],
        revisionFields: ["createdAt", "comment", "title"],
        relationAxes: [],
      },
    ],
  },
};
