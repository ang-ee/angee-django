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

// The folder tree loads lazily: top-level folders first, then a folder's
// children only when it is expanded. Both arms share the projection and bound
// each request with `limit`; the drive's partial indexes on `(drive, parent,
// name)` and `(drive, name) WHERE parent IS NULL` serve them cheaply.
export const StorageFolderRoots = graphql(`
  query StorageFolderRoots($drive: String!, $limit: Int) {
    folders(
      where: { drive: { _eq: $drive }, parent: { _is_null: true } }
      order_by: [{ name: asc }]
      limit: $limit
    ) {
      id
      name
      description
      is_virtual
      drive
      parent
    }
  }
`);

export const StorageFolderChildren = graphql(`
  query StorageFolderChildren($drive: String!, $parent: String!, $limit: Int) {
    folders(
      where: { drive: { _eq: $drive }, parent: { _eq: $parent } }
      order_by: [{ name: asc }]
      limit: $limit
    ) {
      id
      name
      description
      is_virtual
      drive
      parent
    }
  }
`);

export const StorageFileById = graphql(`
  query StorageFileById($id: String!) {
    files_by_pk(id: $id) {
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

/** A stored file, independently fetched for preview by its public id. */
export type StorageFile = NonNullable<
  DocumentType<typeof StorageFileById>["files_by_pk"]
>;

/** A folder (tree node) or smart folder, as projected by the folder-tree
 * queries; the roots and children arms share this shape. Ids are public sqids. */
export type StorageFolder = NonNullable<
  DocumentType<typeof StorageFolderRoots>["folders"]
>[number];

/** A drive (tree root), as projected by `StorageDrives`. */
export type StorageDrive = NonNullable<
  DocumentType<typeof StorageDrives>["drives"]
>[number];
