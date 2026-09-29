import { graphql } from "@angee/gql/console";

/** A phase selection carries the revision shown when the choice was made. */
export const SetProjectCurrentMilestoneDocument = graphql(`
  mutation ProjectsSetCurrentMilestone($id: ID!, $milestone: ID!, $expected_revision: Int) {
    set_project_current_milestone(id: $id, milestone: $milestone, expected_revision: $expected_revision) {
      ok message validation_errors id
    }
  }
`);

/** Audience changes use the task's dedicated permission and revision guard. */
export const SetTaskVisibilityDocument = graphql(`
  mutation ProjectsSetTaskVisibility($id: ID!, $visibility: TaskVisibility!, $expected_revision: Int) {
    set_task_visibility(id: $id, visibility: $visibility, expected_revision: $expected_revision) {
      ok message validation_errors id
    }
  }
`);
