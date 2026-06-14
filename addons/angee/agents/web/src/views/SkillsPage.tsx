import * as React from "react";
import { Column, DataPage, List } from "@angee/base";

const MODEL = "agents.Skill";

// Skills are discovered from a source, not authored here: a read-only list, no
// create and no editable detail.
export function SkillsPage(): React.ReactElement {
  return (
    <DataPage model={MODEL} placement="inline" hideCreate>
      <List model={MODEL} pageSize={50}>
        <Column field="name" />
        <Column field="description" />
        <Column field="path" />
        <Column field="updatedAt" />
      </List>
    </DataPage>
  );
}
