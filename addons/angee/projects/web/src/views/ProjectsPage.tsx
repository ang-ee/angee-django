import { ResourceList } from "@angee/ui";
import * as React from "react";

import { projectListDeclaration, projectRecordTabs, useProjectFormDeclaration } from "../project-declarations";
import { PROJECT_MODEL } from "../resources";

/** Projects collection plus its one routed FormView record surface. */
export function ProjectsPage(): React.ReactElement {
  const form = useProjectFormDeclaration();
  return <ResourceList resource={PROJECT_MODEL} placement="inline" routed recordTabs={projectRecordTabs}>
    {projectListDeclaration}
    {form}
  </ResourceList>;
}
