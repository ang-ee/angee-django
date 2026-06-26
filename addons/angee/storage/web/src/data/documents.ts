// Authored GraphQL for the storage console. Drives, folders, files, and
// backends are read through Hasura-shaped resources; only storage-specific
// verbs are authored here.

import { graphql, type DocumentType } from "@angee/gql/console";

export const StorageFileUploadBegin = graphql(`
  mutation StorageFileUploadBegin($input: FileUploadBeginInput!) {
    file_upload_begin(input: $input) {
      method
      upload_url
      upload_token
      error
      error_code
      file {
        id
        filename
        upload_state
      }
    }
  }
`);

export const StorageFileUploadFinalize = graphql(`
  mutation StorageFileUploadFinalize($input: FileUploadFinalizeInput!) {
    file_upload_finalize(input: $input) {
      error
      error_code
      file {
        id
        filename
        upload_state
      }
    }
  }
`);

export const StorageRestoreFile = graphql(`
  mutation StorageRestoreFile($id: ID!) {
    restore_file(id: $id) {
      id
    }
  }
`);

export const StorageDrives = graphql(`
  query StorageDrives($limit: Int, $offset: Int) {
    drives(limit: $limit, offset: $offset) {
      id
      slug
      name
      description
      is_archived
    }
  }
`);

// Admin-only: the backend catalogue, for the inline drive-create form's backend
// picker. Non-admins get a denied result and an empty list (drive create is
// storage-admin-gated server-side anyway).
export const StorageBackends = graphql(`
  query StorageBackends($limit: Int, $offset: Int) {
    backends(limit: $limit, offset: $offset) {
      id
      slug
      label
    }
  }
`);

export const StorageFolders = graphql(`
  query StorageFolders($limit: Int, $offset: Int) {
    folders(limit: $limit, offset: $offset) {
      id
      name
      description
      is_virtual
      smart_kind
      drive
      parent
    }
  }
`);

export const StorageFiles = graphql(`
  query StorageFiles($limit: Int, $offset: Int) {
    files(limit: $limit, offset: $offset) {
      id
      filename
      title
      size_bytes
      content_hash
      upload_state
      is_trashed
      updated_at
      created_by_label
      url
      drive
      folder
      mime_type {
        mime_type
        category
        label
        icon_key
      }
    }
  }
`);

/** A stored file row, as projected by `StorageFiles`. `drive`/`folder` are the
 * parents' public ids. */
export type StorageFile = NonNullable<
  DocumentType<typeof StorageFiles>["files"]
>[number];

/** A folder (tree node) or smart folder, as projected by `StorageFolders`; ids
 * are public sqids. */
export type StorageFolder = NonNullable<
  DocumentType<typeof StorageFolders>["folders"]
>[number];
