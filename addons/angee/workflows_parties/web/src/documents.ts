// The launcher's two operations: discover the seeded subjectless dedupe
// workflow (published, empty subject declaration) and start a run. The run and
// its review Decision then live entirely on the workflows surfaces (run detail
// + inbox) — this package adds no parallel review UI.

import { graphql } from "@angee/gql/console";

export const SubjectlessWorkflows = graphql(`
  query WorkflowsPartiesSubjectless($subjectDeclaration: String!) {
    workflows_for_subject_declaration(subject_declaration: $subjectDeclaration) {
      id
      key
      name
      subject_declaration
    }
  }
`);

export const StartDedupeRun = graphql(`
  mutation WorkflowsPartiesStartDedupe($id: ID!) {
    start_workflow_run(workflow: $id) {
      ok
      message
      id
    }
  }
`);
