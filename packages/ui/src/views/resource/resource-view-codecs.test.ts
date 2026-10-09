import { schemaFieldMetadataFromDataResources } from "@angee/metadata";
import {
  testDataResource,
  testQueryField,
  testResourceQuery,
} from "@angee/metadata/testing";
import { expect, test } from "vitest";

import { requestedFieldPaths } from "./resource-view-codecs";

test("selects representation leaves for column subline and currency paths", () => {
  const resource = testDataResource("billing.Invoice", {
    query: testResourceQuery({
      fields: {
        amount: testQueryField("amount", { scalar: "Decimal" }),
        "vendor.name": testQueryField("vendor.name", {
          row: {
            path: "vendor.name",
            paths: ["vendor.id", "vendor.name"],
          },
        }),
        currency: testQueryField("currency.code", {
          row: { path: "currency.code", paths: ["currency.code"] },
        }),
      },
    }),
  });
  const metadata = schemaFieldMetadataFromDataResources([resource]).labels[
    resource.modelLabel
  ]!;

  expect(
    requestedFieldPaths(
      [{
        field: "amount",
        subline: "vendor.name",
        currencyField: "currency",
      }],
      undefined,
      metadata,
    ),
  ).toEqual([
    "id",
    "amount",
    "vendor.id",
    "vendor.name",
    "currency.code",
  ]);
});
