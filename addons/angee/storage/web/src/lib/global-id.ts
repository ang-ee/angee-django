// Relation fields (`file.drive`, `folder.parent`, …) come back as the bare public
// sqid, but a node `id` is a base64 relay GlobalID. Normalise bare relation ids
// up so client-side joins match the related row's `id`. The codec is SDK-owned
// (one relay boundary); only the per-addon type constants live here.
export {
  toRelayGlobalId as toGlobalId,
  relationRelayGlobalId as relationGlobalId,
} from "@angee/sdk";

export const DRIVE_TYPE = "DriveType";
export const FOLDER_TYPE = "FolderType";
