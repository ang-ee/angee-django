import { graphql, type DocumentType } from "@angee/gql/console";

export const WorkflowGraphDocument = graphql(`
  query WorkflowGraph($workflow: String!, $workflowId: String!) {
    workflows_by_pk(id: $workflow) {
      id
      name
      status
      version
    }
    workflow_steps(
      where: { workflow: { _eq: $workflowId } }
      order_by: [{ is_entry: desc }, { key: asc }]
    ) {
      id
      key
      name
      step_class
      config
      join_rule
      is_entry
      position
      updated_at
    }
    workflow_edges(
      where: { workflow: { _eq: $workflowId } }
      order_by: [{ source: asc }, { target: asc }, { condition: asc }]
    ) {
      id
      condition
      source {
        id
        key
        name
      }
      target {
        id
        key
        name
      }
    }
  }
`);

export const UpdateWorkflowStepPositionDocument = graphql(`
  mutation UpdateWorkflowStepPosition($id: String!, $position: JSON!) {
    update_workflow_steps_by_pk(
      pk_columns: { id: $id }
      _set: { position: $position }
    ) {
      id
      position
      updated_at
    }
  }
`);

export const CreateWorkflowEdgeDocument = graphql(`
  mutation CreateWorkflowEdge(
    $workflow: ID!
    $source: ID!
    $target: ID!
    $condition: String
  ) {
    insert_workflow_edges_one(
      object: {
        workflow: $workflow
        source: $source
        target: $target
        condition: $condition
      }
    ) {
      id
      condition
      source {
        id
      }
      target {
        id
      }
    }
  }
`);

export const PublishWorkflowDocument = graphql(`
  mutation PublishWorkflow($id: ID!) {
    publish_workflow(workflow: $id) {
      ok
      message
    }
  }
`);

export const StartWorkflowRunDocument = graphql(`
  mutation StartWorkflowRun($id: ID!) {
    start_workflow_run(workflow: $id) {
      ok
      message
    }
  }
`);

export const CancelWorkflowRunDocument = graphql(`
  mutation CancelWorkflowRun($id: ID!) {
    cancel_workflow_run(run: $id) {
      ok
      message
    }
  }
`);

export const WorkflowRunDetailDocument = graphql(`
  query WorkflowRunDetail($run: String!, $runId: String!) {
    workflow_runs_by_pk(id: $run) {
      id
      display_name
      status
      data
      error
      steps_taken
      budget_spent
      wake_at
      created_at
      updated_at
      workflow {
        id
        name
        status
        version
      }
    }
    workflow_step_runs(
      where: { run: { _eq: $runId } }
      order_by: [{ created_at: asc }, { map_index: asc }]
    ) {
      id
      display_name
      system_kind
      map_index
      status
      input
      output
      resume_state
      outcome
      attempt
      wait_until
      wait_event
      error
      stacktrace
      created_at
      updated_at
      step {
        id
        key
        name
        step_class
        position
      }
    }
  }
`);

export type WorkflowGraphData = DocumentType<typeof WorkflowGraphDocument>;
export type WorkflowGraphStep = WorkflowGraphData["workflow_steps"][number];
export type WorkflowGraphEdge = WorkflowGraphData["workflow_edges"][number];
export type WorkflowRunDetailData = DocumentType<typeof WorkflowRunDetailDocument>;
export type WorkflowRunStepRun =
  WorkflowRunDetailData["workflow_step_runs"][number];
