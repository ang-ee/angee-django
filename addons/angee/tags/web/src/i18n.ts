import { createNamespaceT } from "@angee/ui";

// Only the keys the page and tag form resolve — the shell chrome labels live on
// the manifest (index.tsx), and metadata-labelled columns/fields need none.
export const enTagsMessages: Record<string, string> = {
  "col.name": "Name",
  "col.color": "Color",
  "form.details": "Details",
  "record.label": "Tags",
  "record.error": "Could not update tags.",
};

export const useTagsT = createNamespaceT("tags", enTagsMessages);
