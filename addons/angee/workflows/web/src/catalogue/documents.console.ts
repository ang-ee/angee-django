import { graphql } from "@angee/gql/console";

export const WORKFLOW_MODEL = "workflows.Workflow";
export const WORKFLOW_VERSION_MODEL = "workflows.WorkflowVersion";

export const WorkflowDocument = graphql(`
  query WorkflowDetail($id: String!) {
    workflow_by_pk(id: $id) {
      id key name description subject_model
      published { id number created_at }
    }
  }
`);
