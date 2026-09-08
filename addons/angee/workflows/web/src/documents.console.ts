import { graphql, type DocumentType } from "@angee/gql/console";

export const WorkflowGraphDocument = graphql(`
  query WorkflowGraph($workflow: String!) {
    workflows_by_pk(id: $workflow) {
      id
      name
      status
      version
    }
    workflow_steps(
      where: { workflow: { _eq: $workflow } }
      order_by: [{ is_entry: desc }, { key: asc }]
    ) {
      id
      key
      name
      step_class
      config
      config_errors
      join_rule
      is_entry
      position
      updated_at
    }
    workflow_edges(
      where: { workflow: { _eq: $workflow } }
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

export const WorkflowStepOperationsDocument = graphql(`
  query WorkflowStepOperations {
    workflow_step_operations {
      key
      label
      category
      defaults
      config_schema
      input_schema
      output_schema
      input_contract { raw_schema root_node_id nodes { id kind json_type title description nullable } edges { parent_node_id child_node_id kind key } }
      output_contract { raw_schema root_node_id nodes { id kind json_type title description nullable } edges { parent_node_id child_node_id kind key } }
      outcomes { key label description }
      description
      selectable
      effect
      effect_description
      idempotent
      subject_declaration
    }
  }
`);

export const WorkflowDefinitionDocument = graphql(`
  query WorkflowDefinition($workflow: ID!) {
    workflow_definition(workflow: $workflow) {
      revision
      workflow {
        id
        key
        name
        description
        purpose
        subject_declaration
        status
        version
        lineage_id
        error_workflow { id }
        max_steps
        budget
        current_published_version
        publication_status
      }
      nodes {
        id key name step_class config config_errors input_binding join_rule is_entry position
      }
      edges { id source target condition }
      readiness { code message kind id client_key requested_id field detail_path }
    }
  }
`);

export const SaveWorkflowDefinitionDocument = graphql(`
  mutation SaveWorkflowDefinition($workflow: ID!, $expectedRevision: Int!, $edit: WorkflowDefinitionEditInput!) {
    save_workflow_definition(workflow: $workflow, expected_revision: $expectedRevision, edit: $edit) {
      status revision current_revision
      nodes { client_key id }
      edges { client_key id }
      diagnostics { code message kind id client_key requested_id field detail_path }
    }
  }
`);

export const PublishWorkflowDefinitionDocument = graphql(`
  mutation PublishWorkflowDefinition($workflow: ID!, $expectedRevision: Int!) {
    publish_workflow_definition(workflow: $workflow, expected_revision: $expectedRevision) {
      status revision current_revision publication_created
      publication { id version status }
      diagnostics { code message kind id client_key requested_id field detail_path }
    }
  }
`);

export const WorkflowInputSourcesDocument = graphql(`
  query WorkflowInputSources($workflow: ID!, $expectedRevision: Int!, $edit: WorkflowDefinitionEditInput!, $target: WorkflowEndpointInput!) {
    workflow_input_sources(workflow: $workflow, expected_revision: $expectedRevision, edit: $edit, target: $target) {
      status revision current_revision
      diagnostics { code message kind id client_key requested_id field detail_path }
      sources {
        kind id client_key step_key label
        contract { raw_schema root_node_id nodes { id kind json_type title description nullable } edges { parent_node_id child_node_id kind key } }
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

export const WorkflowsForSubjectDeclarationDocument = graphql(`
  query WorkflowsForSubjectDeclaration($subjectDeclaration: String!) {
    workflows_for_subject_declaration(
      subject_declaration: $subjectDeclaration
    ) {
      id
      name
      subject_declaration
    }
  }
`);

export const WorkflowLaunchDocument = graphql(`
  query WorkflowLaunch($id: String!) {
    workflows_by_pk(id: $id) {
      id
      lineage_id
      purpose
      status
      version
      published_from {
        id
      }
      current_published_id
      current_published_version
      current_published_subject_declaration
    }
  }
`);

export const RunWorkflowDocument = graphql(`
  mutation RunWorkflow($workflow: ID!, $subject: WorkflowObjectRefInput) {
    start_workflow_run(workflow: $workflow, subject: $subject) {
      ok
      message
      validation_errors
      id
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
  query WorkflowRunDetail($run: String!) {
    workflow_runs_by_pk(id: $run) {
      id
      display_name
      status
      origin
      error
      steps_taken
      budget_spent
      wake_at
      waiting_kind
      next_wake_at
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
      where: { run: { _eq: $run } }
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
      waiting_kind
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
