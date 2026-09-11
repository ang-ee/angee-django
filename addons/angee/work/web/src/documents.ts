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

// `stage` has a schema default, so this mutation intentionally stays authored:
// generated action hooks only cover verbs whose arguments are all required.
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
