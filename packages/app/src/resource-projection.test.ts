import { testDataResource } from "@angee/metadata/testing";
import { expect, test } from "vitest";

import { resourceLabelI18nMessages } from "./resource-projection";

test.each([
  ["project", "project"],
  ["WorkItem", "work item"],
  ["URLAlias", "url alias"],
])("registers %s as the sentence noun %s", (modelName, expected) => {
  const resource = testDataResource("fixture.Record", { modelName });

  expect(resourceLabelI18nMessages({
    console: { metadata: { angee: { resources: [resource] } } },
  })).toEqual({
    ui: { "console:fixture.Record.console:fixture.Record": expected },
  });
});
