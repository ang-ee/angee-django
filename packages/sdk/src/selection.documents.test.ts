import { buildSchema, parse, validate } from "graphql";
import { describe, expect, test } from "vitest";

import {
  assembleAggregateDocument,
  assembleDetailDocument,
  assembleGroupByDocument,
  assembleListDocument,
  assembleMutationDocument,
  assembleRevisionsDocument,
} from "./selection";
import { changeSubscriptionDocument } from "./relay-invalidation";
import {
  fieldMetadataFromSchema,
  modelMetadataForLabel,
  type ModelRootFieldMetadata,
} from "./model-metadata";

const SDL = /* GraphQL */ `
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

  type IntegrationType implements Node {
    id: ID!
    status: String!
    implClass: String!
  }

  input IntegrationFilter {
    status: StrFilterLookup
  }

  input IntegrationAggregateGroupBySpec {
    field: String!
  }

  input IntegrationAggregateGroupOrder {
    field: String!
    direction: OrderDirection! = ASC
  }

  type IntegrationAggregateGroupKey {
    implClass: String
    status: String
  }

  type IntegrationAggregateGrouped {
    key: IntegrationAggregateGroupKey!
    count: Int!
    filter: JSON!
  }

  type IntegrationAggregateGroupedResult {
    pageInfo: OffsetPaginationInfo!
    totalCount: Int!
    results: [IntegrationAggregateGrouped!]!
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

  type OAuthClient implements Node {
    id: ID!
    displayName: String!
  }

  type OAuthClientOffsetPaginated {
    pageInfo: OffsetPaginationInfo!
    totalCount: Int!
    results: [OAuthClient!]!
  }

  input OAuthClientInput {
    displayName: String!
  }

  input OAuthClientPatch {
    id: ID!
    displayName: String
  }

  type Person implements Node {
    id: ID!
    name: String!
  }

  type PersonOffsetPaginated {
    pageInfo: OffsetPaginationInfo!
    totalCount: Int!
    results: [Person!]!
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

  type ChangeEvent {
    model: String!
    id: ID!
    action: String!
    changedFields: [String!]
    changedValues: JSON
  }

  type Query {
    saleLookup(id: ID!): Sale
    retailSales(
      pagination: OffsetPaginationInput
      filters: SaleFilter
      order: SaleOrder
    ): SaleOffsetPaginated!
    totalSales(filter: SaleFilter): SaleAggregate!
    saleBreakdown(
      groupBy: [SaleGroupBySpec!]!
      pagination: OffsetPaginationInput
      filter: SaleFilter
      orderBy: [SaleGroupOrder!] = null
    ): SaleGroupedResult!
    integrationGroups(
      groupBy: [IntegrationAggregateGroupBySpec!]!
      pagination: OffsetPaginationInput
      filter: IntegrationFilter
      orderBy: [IntegrationAggregateGroupOrder!] = null
    ): IntegrationAggregateGroupedResult!
    saleRevisions(id: ID!): [SaleRevision!]!
    oauthClientRecord(id: ID!): OAuthClient
    identityClients(pagination: OffsetPaginationInput): OAuthClientOffsetPaginated!
    person(id: ID!): Person
    people(pagination: OffsetPaginationInput): PersonOffsetPaginated!
  }

  type Mutation {
    makeSale(data: SaleInput!): Sale!
    reviseSale(data: SalePatch!): Sale!
    removeSale(id: ID!, confirm: Boolean! = false): DeletePreview!
    createOAuthAccount(data: OAuthClientInput!): OAuthClient!
    updateOAuthAccount(data: OAuthClientPatch!): OAuthClient!
    deleteOAuthAccount(id: ID!, confirm: Boolean! = false): DeletePreview!
  }

  type Subscription {
    saleChanged: ChangeEvent!
  }
`;

const schema = buildSchema(SDL);
const metadata = fieldMetadataFromSchema(schema);

/** A document is only correct if it validates against the SDL fixture. */
function expectValid(document: string): void {
  const errors = validate(schema, parse(document));
  expect(errors.map((error) => error.message)).toEqual([]);
}

function rootFields(modelLabel: string): ModelRootFieldMetadata {
  const model = modelMetadataForLabel(metadata, modelLabel);
  if (!model?.rootFields) {
    throw new Error(`Expected root fields for ${modelLabel}.`);
  }
  return model.rootFields;
}

describe("assembleDetailDocument", () => {
  test("queries the schema-declared detail field by relay id", () => {
    const document = assembleDetailDocument("Sale", ["title", "state"], rootFields("Sale"));
    expect(document).toBe(
      "query saleLookup($id: ID!) { saleLookup(id: $id) { id title state } }",
    );
    expectValid(document);
  });
});

describe("assembleRevisionsDocument", () => {
  test("queries the schema-declared revisions field by relay id", () => {
    const document = assembleRevisionsDocument(
      "Sale",
      ["createdAt", "comment", "title"],
      rootFields("Sale"),
    );
    expect(document).toBe(
      "query saleRevisions($id: ID!) { saleRevisions(id: $id) { id createdAt comment title } }",
    );
    expectValid(document);
  });
});

describe("assembleListDocument", () => {
  test("builds the offset page from the schema-declared list field", () => {
    const document = assembleListDocument("Sale", ["title"], rootFields("Sale"));
    expect(document).toBe(
      "query retailSales($pagination: OffsetPaginationInput) { " +
        "retailSales(pagination: $pagination) { " +
        "totalCount results { id title } pageInfo { offset limit } } }",
    );
    expectValid(document);
  });

  test("adds filters and the @oneOf order variable on request", () => {
    const document = assembleListDocument("Sale", ["title"], rootFields("Sale"), {
      withFilter: true,
      withOrder: true,
    });
    expect(document).toContain("$pagination: OffsetPaginationInput");
    expect(document).toContain("$filters: SaleFilter");
    expect(document).toContain("$order: SaleOrder");
    expect(document).toContain("filters: $filters");
    expect(document).toContain("order: $order");
    expectValid(document);
  });

  test("uses acronym model roots by SDL lookup, not casing heuristics", () => {
    const document = assembleListDocument(
      "iam.OAuthClient",
      ["displayName"],
      rootFields("iam.OAuthClient"),
    );
    expect(document).toBe(
      "query identityClients($pagination: OffsetPaginationInput) { " +
        "identityClients(pagination: $pagination) { " +
        "totalCount results { id displayName } pageInfo { offset limit } } }",
    );
    expectValid(document);
  });

  test("uses irregular plural roots by SDL lookup", () => {
    const document = assembleListDocument("people.Person", ["name"], rootFields("people.Person"));
    expect(document).toBe(
      "query people($pagination: OffsetPaginationInput) { " +
        "people(pagination: $pagination) { " +
        "totalCount results { id name } pageInfo { offset limit } } }",
    );
    expectValid(document);
  });
});

describe("assembleMutationDocument", () => {
  test("create uses the schema-declared mutation field", () => {
    const document = assembleMutationDocument("Sale", "create", ["title"], rootFields("Sale"));
    expect(document).toBe(
      "mutation makeSale($data: SaleInput!) { " +
        "makeSale(data: $data) { id title } }",
    );
    expectValid(document);
  });

  test("uses acronym model mutation roots by SDL lookup", () => {
    const document = assembleMutationDocument(
      "iam.OAuthClient",
      "create",
      ["displayName"],
      rootFields("iam.OAuthClient"),
    );
    expect(document).toBe(
      "mutation createOAuthAccount($data: OAuthClientInput!) { " +
        "createOAuthAccount(data: $data) { id displayName } }",
    );
    expectValid(document);
  });

  test("update takes a patch whose id travels inside the data", () => {
    const document = assembleMutationDocument("Sale", "update", ["title"], rootFields("Sale"));
    expect(document).toBe(
      "mutation reviseSale($data: SalePatch!) { " +
        "reviseSale(data: $data) { id title } }",
    );
    expectValid(document);
  });

  test("delete returns the cascade DeletePreview shape", () => {
    const document = assembleMutationDocument("Sale", "delete", [], rootFields("Sale"));
    expect(document).toBe(
      "mutation removeSale($id: ID!, $confirm: Boolean) { removeSale(id: $id, confirm: $confirm) { " +
        "totalDeletedCount hasBlockers " +
        "deleted { label count } updated { label count } blocked { label count } " +
        "root { label objectLabel objectId " +
        "children { label objectLabel objectId " +
        "children { label objectLabel objectId } } } } }",
    );
    expect(document).toContain("confirm: $confirm");
    expect(document).toContain("root { label objectLabel objectId");
    expectValid(document);
  });
});

describe("aggregate documents", () => {
  test("the ungrouped aggregate selects just count", () => {
    const document = assembleAggregateDocument("Sale", rootFields("Sale"));
    expect(document).toBe("query totalSales { totalSales { count } }");
    expectValid(document);
  });

  test("the ungrouped aggregate accepts the model filter on request", () => {
    const document = assembleAggregateDocument("Sale", rootFields("Sale"), {
      withFilter: true,
    });
    expect(document).toBe(
      "query totalSales($filter: SaleFilter) { " +
        "totalSales(filter: $filter) { count } }",
    );
    expectValid(document);
  });

  test("the ungrouped aggregate selects requested measures", () => {
    const document = assembleAggregateDocument("Sale", rootFields("Sale"), {
      measures: [{ op: "sum", field: "amount" }],
    });
    expect(document).toBe(
      "query totalSales { totalSales { count sum { amount } } }",
    );
    expectValid(document);
  });

  test("the grouped aggregate declares groupBy and offset pagination", () => {
    const document = assembleGroupByDocument("Sale", rootFields("Sale"), {
      keyFields: ["state"],
    });
    expect(document).toBe(
      "query saleBreakdown($groupBy: [SaleGroupBySpec!]!, " +
        "$pagination: OffsetPaginationInput) { " +
        "saleBreakdown(groupBy: $groupBy, pagination: $pagination) { " +
        "totalCount results { key { state } count } " +
        "pageInfo { offset limit } } }",
    );
    expect(document).not.toContain("filter");
    expectValid(document);
  });

  test("the grouped aggregate selects the filter echo on request", () => {
    const document = assembleGroupByDocument("Sale", rootFields("Sale"), {
      keyFields: ["state"],
      withFilterEcho: true,
    });
    expect(document).toBe(
      "query saleBreakdown($groupBy: [SaleGroupBySpec!]!, " +
        "$pagination: OffsetPaginationInput) { " +
        "saleBreakdown(groupBy: $groupBy, pagination: $pagination) { " +
        "totalCount results { key { state } count filter } " +
        "pageInfo { offset limit } } }",
    );
    expectValid(document);
  });

  test("the grouped aggregate selects requested measures", () => {
    const document = assembleGroupByDocument("Sale", rootFields("Sale"), {
      keyFields: ["state"],
      measures: [{ op: "sum", field: "amount" }],
    });
    expect(document).toBe(
      "query saleBreakdown($groupBy: [SaleGroupBySpec!]!, " +
        "$pagination: OffsetPaginationInput) { " +
        "saleBreakdown(groupBy: $groupBy, pagination: $pagination) { " +
        "totalCount results { key { state } count sum { amount } } " +
        "pageInfo { offset limit } } }",
    );
    expectValid(document);
  });

  test("the grouped aggregate accepts the model filter on request", () => {
    const document = assembleGroupByDocument("Sale", rootFields("Sale"), {
      keyFields: ["state"],
      withFilter: true,
    });
    expect(document).toBe(
      "query saleBreakdown($groupBy: [SaleGroupBySpec!]!, " +
        "$pagination: OffsetPaginationInput, $filter: SaleFilter) { " +
        "saleBreakdown(groupBy: $groupBy, pagination: $pagination, filter: $filter) { " +
        "totalCount results { key { state } count } " +
        "pageInfo { offset limit } } }",
    );
    expectValid(document);
  });

  test("the grouped aggregate accepts group ordering on request", () => {
    const document = assembleGroupByDocument("Sale", rootFields("Sale"), {
      keyFields: ["createdAtMonth"],
      withOrderBy: true,
    });
    expect(document).toBe(
      "query saleBreakdown($groupBy: [SaleGroupBySpec!]!, " +
        "$pagination: OffsetPaginationInput, $orderBy: [SaleGroupOrder!]) { " +
        "saleBreakdown(groupBy: $groupBy, pagination: $pagination, orderBy: $orderBy) { " +
        "totalCount results { key { createdAtMonth } count } " +
        "pageInfo { offset limit } } }",
    );
    expectValid(document);
  });

  test("the grouped aggregate uses schema-declared group input and order input types", () => {
    const document = assembleGroupByDocument(
      "integrate.Integration",
      rootFields("integrate.Integration"),
      {
        keyFields: ["implClass"],
        withFilter: true,
        withOrderBy: true,
      },
    );
    expect(document).toBe(
      "query integrationGroups($groupBy: [IntegrationAggregateGroupBySpec!]!, " +
        "$pagination: OffsetPaginationInput, $filter: IntegrationFilter, " +
        "$orderBy: [IntegrationAggregateGroupOrder!]) { " +
        "integrationGroups(groupBy: $groupBy, pagination: $pagination, " +
        "filter: $filter, orderBy: $orderBy) { " +
        "totalCount results { key { implClass } count } " +
        "pageInfo { offset limit } } }",
    );
    expectValid(document);
  });
});

describe("changeSubscriptionDocument", () => {
  test("subscribes to the model's change event", () => {
    const document = changeSubscriptionDocument("Sale");
    expect(document).toBe(
      "subscription angeeSaleChanged { " +
        "saleChanged { model id action changedFields changedValues } }",
    );
    expectValid(document);
  });
});
