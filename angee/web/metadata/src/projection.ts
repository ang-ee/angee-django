import type {
  AngeeSchemaMetadata,
  DataResourceMetadata,
} from "./artifact";

export function dataResourcesFromAngeeSchemaMetadata(
  metadata: AngeeSchemaMetadata | undefined,
): readonly DataResourceMetadata[] {
  return metadata?.angee?.resources ?? [];
}
