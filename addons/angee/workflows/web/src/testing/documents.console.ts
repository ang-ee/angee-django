import { graphql, type DocumentType } from "@angee/gql/console";

const RunFields = graphql(`
  fragment WorkflowRunFixture on WorkflowRunType {
    id status origin outcome error input output created_at finished_at
    can_cancel can_reprocess run_as { id display_name }
    subject_model subject_id reprocess_of { id }
    parent_step { id run { id } }
    version { id number workflow { id key name subject_model } }
  }
`);
const StepRunFields = graphql(`
  fragment WorkflowStepRunFixture on StepRunType {
    id node_key map_index is_mapped is_map map_settled map_total rank status outcome attempt waiting_kind wait_reason input output
    can_retry requires_duplicate_acknowledgement
    awaited_run { id }
    attempts { id number result started_at finished_at error stacktrace }
    artifacts { id label model_label record_id }
  }
`);
const WorkflowFields = graphql(`
  fragment WorkflowFixture on WorkflowType {
    id key name description subject_model
    published { id number created_at }
  }
`);

export type Run = DocumentType<typeof RunFields>;
export type StepRun = DocumentType<typeof StepRunFields>;
export type Workflow = DocumentType<typeof WorkflowFields>;
