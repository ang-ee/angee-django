import { ResourceList } from "@angee/ui";
import * as React from "react";

import { projectListDeclaration } from "../project-declarations";
import { projectForm } from "../record-forms";
import { PROJECT_MODEL } from "../resources";

/** Projects collection plus its one routed FormView record surface. */
export function ProjectsPage(): React.ReactElement {
  return <ResourceList resource={PROJECT_MODEL} placement="inline" routed form={projectForm}>
    {projectListDeclaration}
  </ResourceList>;
}
