import type { ModelMetadata } from "./artifact";

export interface RecordSubtitleFields {
  created?: string;
  updated?: string;
  wordCount?: string;
}

const CREATED_FIELD_CANDIDATES = ["createdAt", "created_at", "created"] as const;
const UPDATED_FIELD_CANDIDATES = ["updatedAt", "updated_at", "updated"] as const;
const WORD_COUNT_FIELD_CANDIDATES = ["wordCount", "word_count", "words"] as const;

export function recordSubtitleFields(
  metadata: ModelMetadata | null | undefined,
): RecordSubtitleFields {
  return {
    created: firstMetadataField(metadata, CREATED_FIELD_CANDIDATES),
    updated: firstMetadataField(metadata, UPDATED_FIELD_CANDIDATES),
    wordCount: firstMetadataField(metadata, WORD_COUNT_FIELD_CANDIDATES),
  };
}

function firstMetadataField(
  metadata: ModelMetadata | null | undefined,
  candidates: readonly string[],
): string | undefined {
  if (!metadata) return undefined;
  for (const candidate of candidates) {
    if (metadata.fields[candidate]) return candidate;
  }
  return undefined;
}
