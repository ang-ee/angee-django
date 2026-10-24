import { graphql, type DocumentType } from "@angee/gql/console";
export const BackupPlanPreviewDocument = graphql(`
  query BackupPlanPreview($id: ID, $selection: JSON) {
    preview_backup_plan(id: $id, selection: $selection) {
      version discovered_at selection estimated_bytes has_unknown_sizes
      resources { group name status reason estimated_bytes }
    }
  }
`);
export type BackupPreview = DocumentType<typeof BackupPlanPreviewDocument>["preview_backup_plan"];
/** Archive download and integrity facts are read directly from the authorized File owner. */
export const BackupArchiveFileDocument = graphql(`
  query BackupArchiveFile($id: String!) {
    files_by_pk(id: $id) { id filename url size_bytes content_hash is_trashed }
  }
`);

/** One configured archive drive per deployment, shared across plan forms. */
export const BackupDestinationDocument = graphql(`
  query BackupDestination {
    backup_destination_drive
  }
`);

export const BackupActionResultFields = graphql(`
  fragment BackupActionResultFields on ActionResult { ok message id validation_errors }
`);

/** Optional expected selection keeps compatibility while guarding the accepted discovery snapshot. */
export const BackupRunDocument = graphql(`
  mutation BackupRun($id: ID!, $requestKey: String!, $expectedSelection: JSON) {
    run_backup_plan(id: $id, request_key: $requestKey, expected_selection: $expectedSelection) {
      ...BackupActionResultFields
    }
  }
`);
