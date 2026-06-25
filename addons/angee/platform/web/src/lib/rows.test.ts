import { describe, expect, test } from "vitest";

import type { PlatformModelData } from "../documents";
import { fieldRows, modelRows } from "./rows";

const iamModel: PlatformModelData = {
  label: "iam.user",
  app_label: "iam",
  model_name: "User",
  verbose_name: "user",
  db_table: "iam_user",
  addon_id: "angee.iam",
  addon_label: "iam",
  resource_type: "auth/user",
  field_count: 2,
  relation_count: 1,
  depends_on: ["iam.user"],
  fields: [
    { name: "id", attname: "id", kind: "BigAutoField", is_relation: false, relation_target: null, addon: "iam" },
    { name: "created_by", attname: "created_by_id", kind: "ForeignKey", is_relation: true, relation_target: "iam.user", addon: "iam" },
  ],
};

describe("platform row projectors", () => {
  test("modelRows blanks a missing resource type", () => {
    const [row] = modelRows([{ ...iamModel, resource_type: null }]);
    expect(row?.id).toBe("iam.user");
    expect(row?.resourceType).toBe("");
  });

  test("fieldRows flattens fields with composite ids", () => {
    const rows = fieldRows([iamModel]);
    expect(rows).toHaveLength(2);
    expect(rows[0]?.id).toBe("iam.user.id");
    expect(rows[1]?.relationTarget).toBe("iam.user");
  });
});
