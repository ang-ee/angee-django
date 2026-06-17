import { describe, expect, test } from "vitest";

import type { PlatformAddonData, PlatformModelData } from "../documents";
import { addonRows, fieldRows, modelRows } from "./rows";

const iamModel: PlatformModelData = {
  label: "iam.user",
  appLabel: "iam",
  modelName: "User",
  verboseName: "user",
  dbTable: "iam_user",
  addonId: "angee.iam",
  addonLabel: "iam",
  resourceType: "auth/user",
  fieldCount: 2,
  relationCount: 1,
  dependsOn: ["iam.user"],
  fields: [
    { name: "id", attname: "id", kind: "BigAutoField", isRelation: false, relationTarget: null, addon: "iam" },
    { name: "created_by", attname: "created_by_id", kind: "ForeignKey", isRelation: true, relationTarget: "iam.user", addon: "iam" },
  ],
};

describe("platform row projectors", () => {
  test("addonRows maps counts and joins dependsOn", () => {
    const addon: PlatformAddonData = {
      id: "angee.iam",
      label: "iam",
      namespace: "angee",
      kind: "required",
      modelCount: 1,
      fieldCount: 15,
      resourceCount: 0,
      dependsOn: ["angee.graphql", "angee.resources"],
      modelLabels: ["iam.user"],
    };
    const [row] = addonRows([addon]);
    expect(row?.id).toBe("angee.iam");
    expect(row?.addon).toBe("iam");
    expect(row?.models).toBe(1);
    expect(row?.resources).toBe(0);
    expect(row?.dependsOn).toBe("angee.graphql, angee.resources");
  });

  test("modelRows blanks a missing resource type", () => {
    const [row] = modelRows([{ ...iamModel, resourceType: null }]);
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
