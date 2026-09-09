import * as React from "react";

import { WorkflowApprovals } from "./WorkflowApprovals";

/** Global approvals use the same bounded collection and exact detail as run and session views. */
export function InboxPage(): React.ReactElement {
  return <WorkflowApprovals />;
}
