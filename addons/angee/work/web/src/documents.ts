import { graphql } from "@angee/gql/console";

export const WorkQueueContextDocument = graphql(`
  query WorkQueueContext($id: String!) {
    work_queues_by_pk(id: $id) {
      id
      name
      key
      triage_enabled
      cycles_enabled
      estimate_scale
    }
    work_stages(
      where: { queue: { _eq: $id }, category: { _eq: "triage" } }
      limit: 1
    ) {
      id
    }
  }
`);

export const WorkTaskContextDocument = graphql(`
  query WorkTaskContext($id: String!) {
    project_tasks_by_pk(id: $id) {
      id
      stage {
        id
        category
        rule_owned
      }
    }
  }
`);

export const WorkCycleContextDocument = graphql(`
  query WorkCycleContext($id: String!) {
    work_cycles_by_pk(id: $id) {
      id
      name
      number
      starts_on
      ends_on
      completed_at
    }
  }
`);

// Optional/defaulted action arguments are derived only for an `id: ID!` target;
// this `task`-scoped verb with an optional `stage` stays authored.
export const AcceptTaskDocument = graphql(`
  mutation WorkAcceptTask($task: ID!, $stage: ID!) {
    accept_task(task: $task, stage: $stage) {
      ok
      message
      id
      validation_errors
    }
  }
`);

/** Required decline reason keeps this verb authored rather than generated. */
export const DeclineTaskDocument = graphql(`
  mutation WorkDeclineTask($task: ID!, $reason: TaskDroppedReason!, $expected_revision: Int) {
    decline_task(task: $task, reason: $reason, expected_revision: $expected_revision) {
      ok message id validation_errors
    }
  }
`);
