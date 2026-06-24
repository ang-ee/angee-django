import { buildSchema, parse, validate } from "graphql";
import { describe, expect, test } from "vitest";

import { changeSubscriptionDocument } from "./relay-invalidation";

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

/** A document is only correct if it validates against the SDL fixture. */
function expectValid(document: string): void {
  const errors = validate(schema, parse(document));
  expect(errors.map((error) => error.message)).toEqual([]);
}

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
