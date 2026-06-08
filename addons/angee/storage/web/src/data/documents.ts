// Authored GraphQL for the storage console. Drives, folders, and files are read
// through their offset-paginated console queries; the browser fetches each once
// and scopes client-side (see `file-rows.ts`). Mutations (upload, folders) land
// in a later slice.

// A `type` alias (not an `interface`) so it carries an implicit index signature
// and satisfies the authored-hook `Variables` (`Record<string, unknown>`) bound.
export type OffsetPaginationVariables = {
  pagination: { offset: number; limit: number };
};

/** One MIME taxonomy row, as projected on a file. */
export interface StorageMimeType {
  mimeType: string;
  category: string;
  label: string;
  iconKey: string;
}

/** A drive — the unit of access control and key namespace. */
export interface StorageDrive {
  id: string;
  slug: string;
  name: string;
  description: string;
  isArchived: boolean;
}

/** A folder (tree node) or smart folder (e.g. Trash); ids are public sqids. */
export interface StorageFolder {
  id: string;
  name: string;
  description: string;
  isVirtual: boolean;
  smartKind: string;
  drive: string | null;
  parent: string | null;
}

/** A stored file row. `drive`/`folder` are the parents' public ids. */
export interface StorageFile {
  id: string;
  filename: string;
  title: string;
  sizeBytes: number;
  contentHash: string;
  uploadState: string;
  isTrashed: boolean;
  updatedAt: string;
  createdByLabel: string | null;
  url: string;
  drive: string;
  folder: string | null;
  mimeType: StorageMimeType | null;
}

export interface StorageDrivesData {
  drives: { results: StorageDrive[] };
}

export interface StorageFoldersData {
  folders: { results: StorageFolder[] };
}

export interface StorageFilesData {
  files: { results: StorageFile[] };
}

export const STORAGE_DRIVES_QUERY = `
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
`;

export const STORAGE_FOLDERS_QUERY = `
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
`;

export const STORAGE_FILES_QUERY = `
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
`;
