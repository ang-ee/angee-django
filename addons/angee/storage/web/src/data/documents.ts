// Authored GraphQL for the storage console. Drives, folders, and files are read
// through their offset-paginated console queries; the browser fetches each once
// and scopes client-side (see `file-rows.ts`). Standard CRUD mutations are
// emitted by the SDK; only storage-specific verbs are authored here.

import { graphql, type DocumentType } from "@angee/gql/console";

export const StorageFileUploadBegin = graphql(`
  mutation StorageFileUploadBegin($input: FileUploadBeginInput!) {
    fileUploadBegin(input: $input) {
      method
      uploadUrl
      uploadToken
      error
      errorCode
      file {
        id
        filename
        uploadState
      }
    }
  }
`);

export const StorageFileUploadFinalize = graphql(`
  mutation StorageFileUploadFinalize($input: FileUploadFinalizeInput!) {
    fileUploadFinalize(input: $input) {
      error
      errorCode
      file {
        id
        filename
        uploadState
      }
    }
  }
`);

export const StorageRestoreFile = graphql(`
  mutation StorageRestoreFile($id: ID!) {
    restoreFile(id: $id) {
      id
    }
  }
`);

export const StorageDrives = graphql(`
  query StorageDrives($pagination: OffsetPaginationInput) {
    drives(pagination: $pagination) {
      results {
        id
        slug
        name
        description
        isArchived
      }
    }
  }
`);

// Admin-only: the backend catalogue, for the inline drive-create form's backend
// picker. Non-admins get a denied result and an empty list (drive create is
// storage-admin-gated server-side anyway).
export const StorageBackends = graphql(`
  query StorageBackends($pagination: OffsetPaginationInput) {
    backends(pagination: $pagination) {
      results {
        id
        slug
        label
      }
    }
  }
`);

export const StorageFolders = graphql(`
  query StorageFolders($pagination: OffsetPaginationInput) {
    folders(pagination: $pagination) {
      results {
        id
        name
        description
        isVirtual
        smartKind
        drive
        parent
      }
    }
  }
`);

export const StorageFiles = graphql(`
  query StorageFiles($pagination: OffsetPaginationInput) {
    files(pagination: $pagination) {
      results {
        id
        filename
        title
        sizeBytes
        contentHash
        uploadState
        isTrashed
        updatedAt
        createdByLabel
        url
        drive
        folder
        mimeType {
          mimeType
          category
          label
          iconKey
        }
      }
    }
  }
`);

/** A stored file row, as projected by `StorageFiles`. `drive`/`folder` are the
 * parents' public ids. */
export type StorageFile = NonNullable<
  DocumentType<typeof StorageFiles>["files"]
>["results"][number];

/** A folder (tree node) or smart folder, as projected by `StorageFolders`; ids
 * are public sqids. */
export type StorageFolder = NonNullable<
  DocumentType<typeof StorageFolders>["folders"]
>["results"][number];
