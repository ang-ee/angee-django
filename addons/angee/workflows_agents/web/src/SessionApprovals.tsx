import * as React from "react";
import { WorkflowSubjectHistoryPane } from "@angee/workflows";

export function SessionApprovals({ sessionId }: { sessionId: string }): React.ReactElement {
  return <WorkflowSubjectHistoryPane
    subjectDeclaration="agents.AgentSession"
    subjectId={sessionId}
    presentation="collapsible"
  />;
}
