import { graphql, type DocumentType } from "@angee/gql/console";

export const WorkflowGraphDocument = graphql(`
  query WorkflowGraph($workflow: String!) {
    workflows_by_pk(id: $workflow) {
      id
      name
      status
      version
      draft_revision
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
      map_body_operation
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

export const WorkflowDefinitionComparisonDocument = graphql(`
  query WorkflowDefinitionComparison($workflow: ID!, $source: ID!) {
    workflow_definition_comparison(workflow: $workflow, source: $source) {
      source_id source_version source_status draft_id draft_revision
      counts {
        steps_added steps_removed steps_changed connections_added
        connections_removed settings_changed
      }
      changes { kind change key field before after presentation_only }
      source_nodes { key name step_class is_entry }
      source_edges { source target condition }
      draft_nodes { key name step_class is_entry }
      draft_edges { source target condition }
    }
  }
`);

export const RestoreWorkflowDefinitionDocument = graphql(`
  mutation RestoreWorkflowDefinition($workflow: ID!, $source: ID!, $expectedRevision: Int!) {
    restore_workflow_definition(
      workflow: $workflow source: $source expected_revision: $expectedRevision
    ) {
      status current_revision
      snapshot {
        workflow {
          id key name description purpose subject_declaration status version
          draft_revision lineage_id error_workflow { id } max_steps budget
          current_published_version publication_status
        }
        revision
        nodes { id key name step_class config config_errors input_binding join_rule is_entry position }
        edges { id source target condition }
        readiness { code message kind id client_key requested_id field detail_path }
      }
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

export const TestWorkflowDefinitionDocument = graphql(`
  mutation TestWorkflowDefinition(
    $workflow: ID!
    $expectedRevision: Int!
    $requestKey: String!
    $subject: WorkflowObjectRefInput
    $input: JSON
    $scope: WorkflowTestScope!
    $sourceStep: ID
    $fixtures: [WorkflowTestFixtureInput!]
    $repairSourceAttempt: ID
  ) {
    start_workflow_test(
      workflow: $workflow
      expected_revision: $expectedRevision
      request_key: $requestKey
      subject: $subject
      input: $input
      scope: $scope
      source_step: $sourceStep
      fixtures: $fixtures
      repair_source_attempt: $repairSourceAttempt
    ) { ok message validation_errors id }
  }
`);

export const WorkflowTestPlanDocument = graphql(`
  query WorkflowTestPlan(
    $workflow: ID!
    $expectedRevision: Int!
    $scope: WorkflowTestScope!
    $sourceStep: ID
    $subject: WorkflowObjectRefInput
    $input: JSON
    $fixtures: [WorkflowTestFixtureInput!]
    $previousRun: ID
  ) {
    workflow_test_plan(
      workflow: $workflow
      expected_revision: $expectedRevision
      scope: $scope
      selected_step: $sourceStep
      subject: $subject
      input: $input
      fixtures: $fixtures
      previous_run: $previousRun
    ) {
      revision scope source_step_id snapshot_step_id requires_map_item is_current
      operations { step_id key label effect effect_description replaced_by_output outcomes { key label description } }
      required_fixtures { role step_id step_key item_index_required satisfied }
      diagnostics { code message kind id client_key requested_id field detail_path }
      freshness { code step_key field }
    }
  }
`);

export const WorkflowTestFixtureSourcesDocument = graphql(`
  query WorkflowTestFixtureSources(
    $workflow: ID!
    $role: WorkflowTestFixtureRole!
    $stepKey: String!
    $itemIndex: Int
    $after: ID
    $first: Int = 20
  ) {
    workflow_test_fixture_sources(
      workflow: $workflow role: $role step_key: $stepKey item_index: $itemIndex
      after: $after first: $first
    ) {
      items { attempt_id run_id workflow_id workflow_revision step_id step_key role item_index outcome recorded_at }
      next_after
    }
  }
`);

export const WorkflowTestFixtureSourceDocument = graphql(`
  query WorkflowTestFixtureSource(
    $workflow: ID!
    $attempt: ID!
    $role: WorkflowTestFixtureRole!
    $stepKey: String!
    $itemIndex: Int
  ) {
    workflow_test_fixture_source(
      workflow: $workflow attempt: $attempt role: $role step_key: $stepKey item_index: $itemIndex
    ) {
      summary { attempt_id run_id workflow_id workflow_revision step_id step_key role item_index outcome recorded_at }
      value_present value
    }
  }
`);

export const WorkflowRecoveryPlanDocument = graphql(`
  query WorkflowRecoveryPlan($sourceAttempt: ID!) {
    workflow_recovery_plan(source_attempt: $sourceAttempt) {
      attempt_id run_id workflow_id workflow_revision step_id step_key map_index
      available mode unavailable_reason
    }
  }
`);

export const StartWorkflowRecoveryDocument = graphql(`
  mutation StartWorkflowRecovery($sourceAttempt: ID!, $requestKey: String!) {
    start_workflow_recovery(source_attempt: $sourceAttempt, request_key: $requestKey) {
      ok message validation_errors id
    }
  }
`);

export const WorkflowAttemptArtifactsDocument = graphql(`
  query WorkflowAttemptArtifacts($attempt: String!) {
    workflow_step_attempts(where: { id: { _eq: $attempt } }, limit: 1) {
      id artifacts_present
    }
    workflow_step_artifacts_aggregate(where: { attempt: { _eq: $attempt } }) {
      aggregate { count }
    }
  }
`);

export const WorkflowTestRepairContextDocument = graphql(`
  query WorkflowTestRepairContext($sourceAttempt: ID!) {
    workflow_test_repair_context(source_attempt: $sourceAttempt) {
      source_attempt_id source_run_id source_workflow_id source_revision
      draft_workflow_id draft_revision source_step_key source_step_id current_source_step_id
      subject { model id }
      input_present input
      fixtures {
        attempt_id run_id workflow_id workflow_revision step_id step_key
        role item_index outcome recorded_at
      }
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

export const WorkflowMapBodyCandidatesDocument = graphql(`
  query WorkflowMapBodyCandidates($workflow: ID!, $expectedRevision: Int!, $edit: WorkflowDefinitionEditInput!, $owner: WorkflowEndpointInput!) {
    workflow_map_body_candidates(workflow: $workflow, expected_revision: $expectedRevision, edit: $edit, owner: $owner) {
      status revision current_revision
      diagnostics { code message kind id client_key requested_id field detail_path }
      candidates { id client_key step_key label eligible reason }
    }
  }
`);

export const WorkflowTriggerAuthoringDocument = graphql(`
  query WorkflowTriggerAuthoring {
    workflow_trigger_declarations { kind label config_schema }
    workflow_trigger_publishers { model label }
  }
`);

export const WorkflowSchedulePreviewDocument = graphql(`
  query WorkflowSchedulePreview($config: JSON!, $count: Int = 3) {
    workflow_schedule_preview(config: $config, count: $count) {
      timezone
      occurrences
      errors
    }
  }
`);

export const EnableWorkflowTriggerDocument = graphql(`
  mutation EnableWorkflowTrigger($trigger: ID!) {
    enable_workflow_trigger(trigger: $trigger) { ok message validation_errors id }
  }
`);

export const DisableWorkflowTriggerDocument = graphql(`
  mutation DisableWorkflowTrigger($trigger: ID!) {
    disable_workflow_trigger(trigger: $trigger) { ok message validation_errors id }
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
      draft_revision
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

export const ReprocessWorkflowRunDocument = graphql(`
  mutation ReprocessWorkflowRun($run: ID!, $requestKey: String!) {
    reprocess_workflow_run(run: $run, request_key: $requestKey) {
      ok message id validation_errors
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
      occurrence_id
      test_repair_source_attempt { id step_run { id run { id } } }
      recovery_source_attempt { id step_run { id run { id } } }
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
        draft_revision
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

export const WorkflowRunInspectionDocument = graphql(`
  query WorkflowRunInspection($run: String!) {
    workflow_runs_by_pk(id: $run) {
      id origin occurrence_id status waiting_kind next_wake_at error
      test_repair_source_attempt { id step_run { id run { id } } }
      recovery_source_attempt { id step_run { id run { id } } }
      workflow { id name status version draft_revision }
    }
    workflow_step_runs_groups(
      group_by: [{field: STEP}, {field: STATUS}]
      where: {run: {_eq: $run}}
    ) {
      key { step_id status }
      aggregate { count }
    }
    workflow_step_runs_aggregate(where: {run: {_eq: $run}}) {
      aggregate { count }
    }
    failed_step_runs: workflow_step_runs(
      where: {run: {_eq: $run}, status: {_eq: "FAILED"}}
      order_by: [{updated_at: desc}]
      limit: 1
    ) {
      id system_kind map_index error
      step { id key name }
      current_attempt { id error }
    }
  }
`);

export const WorkflowAttemptPayloadDocument = graphql(`
  query WorkflowAttemptPayload(
    $attempt: String!
    $stepRun: String!
    $includeInput: Boolean!
    $includeOutput: Boolean!
    $includeCheckpoint: Boolean!
    $includeFailure: Boolean!
  ) {
    workflow_step_attempts(
      where: {id: {_eq: $attempt}, step_run: {_eq: $stepRun}}
      limit: 1
    ) {
      id
      input_present
      input @include(if: $includeInput)
      output_present
      output @include(if: $includeOutput)
      checkpoint_present
      checkpoint @include(if: $includeCheckpoint)
      error @include(if: $includeFailure)
      stacktrace @include(if: $includeFailure)
    }
  }
`);

export const WorkflowLegacyExecutionPayloadDocument = graphql(`
  query WorkflowLegacyExecutionPayload(
    $run: String!
    $execution: String!
    $includeInput: Boolean!
    $includeOutput: Boolean!
    $includeFailure: Boolean!
  ) {
    workflow_step_runs(
      where: {id: {_eq: $execution}, run: {_eq: $run}}
      limit: 1
    ) {
      id
      input @include(if: $includeInput)
      output @include(if: $includeOutput)
      error @include(if: $includeFailure)
      stacktrace @include(if: $includeFailure)
    }
  }
`);

export const WorkflowInspectionSelectionDocument = graphql(`
  query WorkflowInspectionSelection($run: String!, $execution: String!, $attempt: String!) {
    workflow_step_runs(
      where: {id: {_eq: $execution}, run: {_eq: $run}}
      limit: 1
    ) {
      id
      step { id key name }
      system_kind
      map_index
      status
      outcome
      current_attempt { id }
    }
    workflow_step_attempts(
      where: {id: {_eq: $attempt}, step_run: {_eq: $execution}}
      limit: 1
    ) {
      id
    }
    workflow_step_attempts_aggregate(where: {step_run: {_eq: $execution}}) {
      aggregate { count }
    }
  }
`);

export const WorkflowStepRunCandidateDocument = graphql(`
  query WorkflowStepRunCandidate($run: String!, $step: String!) {
    workflow_step_runs(
      where: {run: {_eq: $run}, step: {_eq: $step}}
      order_by: [{created_at: desc}]
      limit: 2
    ) { id }
  }
`);

export const WorkflowEventConditionDraftDocument = graphql(`
  query WorkflowEventConditionDraft(
    $model: String!
    $condition: JSON
    $clauses: [WorkflowEventConditionClauseInput!]
    $opaque: JSON
  ) {
    workflow_event_condition_draft(
      model: $model
      condition: $condition
      clauses: $clauses
      opaque: $opaque
    ) {
      fields {
        name
        label
        scalar
        lookups { name key label value_schema }
      }
      clauses { field lookup value source_key }
      opaque
      condition
      errors
    }
  }
`);


export type WorkflowGraphData = DocumentType<typeof WorkflowGraphDocument>;
export type WorkflowGraphStep = WorkflowGraphData["workflow_steps"][number];
export type WorkflowGraphEdge = WorkflowGraphData["workflow_edges"][number];
export type WorkflowRunDetailData = DocumentType<typeof WorkflowRunDetailDocument>;
export type WorkflowRunStepRun =
  WorkflowRunDetailData["workflow_step_runs"][number];
