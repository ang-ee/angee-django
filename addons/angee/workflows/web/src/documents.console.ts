import { graphql, type DocumentType } from "@angee/gql/console";

export const RUN_MODEL = "workflows.WorkflowRun";
export const STEP_RUN_MODEL = "workflows.StepRun";
export const STEP_EVIDENCE_MODELS = ["workflows.StepAttempt", "workflows.StepArtifact"] as const;
export const RUN_MODELS = [RUN_MODEL, STEP_RUN_MODEL, ...STEP_EVIDENCE_MODELS] as const;

export const RunDocument = graphql(`
  query WorkflowRunDetail($id: String!) {
    workflowrun_by_pk(id: $id) {
      id status origin outcome error input output created_at finished_at
      can_cancel can_reprocess run_as { id display_name }
      subject_model subject_id reprocess_of { id }
      version { id number workflow { id key name subject_model } }
    }
  }
`);

export const StepRunDocument = graphql(`
  query WorkflowStepRunDetail($id: String!) {
    steprun_by_pk(id: $id) {
      id node_key map_index is_mapped rank status outcome attempt waiting_kind wait_reason input output
      can_retry requires_duplicate_acknowledgement
      attempts { id number result started_at finished_at error stacktrace }
      artifacts { id label model_label record_id }
    }
  }
`);

export const DecisionWaitingRunsDocument = graphql(`
  query DecisionWaitingRuns($group: String!) {
    steprun(where: { decision_group: { _eq: $group }, status: { _eq: "waiting" } }, order_by: [{ rank: asc }, { map_index: asc }]) {
      id node_key map_index is_mapped
      run { id version { workflow { key name } } }
    }
  }
`);

export type Run = NonNullable<DocumentType<typeof RunDocument>["workflowrun_by_pk"]>;
export type StepRun = NonNullable<DocumentType<typeof StepRunDocument>["steprun_by_pk"]>;
