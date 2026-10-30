// Authored GraphQL for the storage console. Drives, folders, files, and
// backends are read through Hasura-shaped resources; only storage-specific
// verbs are authored here.

import { graphql, type DocumentType } from "@angee/gql/console";

/** The record edge declared by attachment-capable resource metadata. */
export const FILE_ATTACHMENT_MODEL = "storage.FileAttachment";

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

export const StorageFoldersPage = graphql(`
  query StorageFoldersPage($where: folders_bool_exp!, $order: [folders_order_by!], $limit: Int!, $offset: Int!) {
    folders(where: $where, order_by: $order, limit: $limit, offset: $offset) {
      id name description is_virtual drive parent
    }
    folders_aggregate(where: $where) { aggregate { count } }
  }
`);

export const StorageDriveById = graphql(`
  query StorageDriveById($id: String!) {
    drives_by_pk(id: $id) { id name }
  }
`);

// A requested scope is independent of whichever sibling page is visible.
export const StorageFolderById = graphql(`
  query StorageFolderById($id: String!) {
    folders_by_pk(id: $id) { id name description is_virtual drive parent
      ancestors { id name description is_virtual drive parent }
    }
  }
`);

export const StorageFileById = graphql(`
  query StorageFileById($id: String!) {
    files_by_pk(id: $id) {
      id
      permissions
      filename
      title
      size_bytes
      content_hash
      upload_state
      visibility
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

export const StorageRecordFiles = graphql(`
  query StorageRecordFiles($modelLabel: String!, $recordId: ID!) {
    record_files(model_label: $modelLabel, record_id: $recordId) {
      can_upload
      attachments {
        id
        label
        file { id filename title size_bytes created_by_label }
      }
    }
  }
`);

export const StorageFileAttachments = graphql(`
  query StorageFileAttachments($file: ID!) {
    file_attachments(file: $file) { id model_label record_id }
  }
`);

/** A stored file, independently fetched for preview by its public id. */
export type StorageFile = NonNullable<
  DocumentType<typeof StorageFileById>["files_by_pk"]
>;

/** A folder (tree node) or smart folder, as projected by the folder-tree
 * queries; the roots and children arms share this shape. Ids are public sqids. */
export type StorageFolder = NonNullable<
  DocumentType<typeof StorageFoldersPage>["folders"]
>[number];

/** The drive selector needs only public identity and its declared label. */
export interface StorageDrive { id: string; name: string }
