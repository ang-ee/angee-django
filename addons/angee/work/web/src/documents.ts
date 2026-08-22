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

export const DeclineTaskDocument = graphql(`
  mutation WorkDeclineTask($task: ID!, $reason: TaskDroppedReason!) {
    decline_task(task: $task, reason: $reason) {
      ok
      message
      id
      validation_errors
    }
  }
`);

export const SnoozeTaskDocument = graphql(`
  mutation WorkSnoozeTask($task: ID!, $until: DateTime!) {
    snooze_task(task: $task, until: $until) {
      ok
      message
      id
      validation_errors
    }
  }
`);

export const MarkTaskDuplicateDocument = graphql(`
  mutation WorkMarkTaskDuplicate($task: ID!, $canonical: ID!) {
    mark_task_duplicate(task: $task, canonical: $canonical) {
      ok
      message
      id
      validation_errors
    }
  }
`);

export const CloseWorkCycleDocument = graphql(`
  mutation WorkCloseCycle($cycle: ID!) {
    close_work_cycle(cycle: $cycle) {
      ok
      message
      id
      validation_errors
    }
  }
`);
