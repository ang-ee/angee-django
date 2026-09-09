import { graphql, type DocumentType } from "@angee/gql/console";

export const AgentSessionWorkflowRunDocument = graphql(`
  query AgentSessionWorkflowRun($session: ID!) {
    agent_session_workflow_run(session: $session)
  }
`);

export type AgentSessionWorkflowRun = DocumentType<typeof AgentSessionWorkflowRunDocument>;
