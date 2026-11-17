import { createNamespaceT } from "@angee/ui/i18n";

// Only the keys the pages, the tag form and the record widget resolve — the shell
// chrome labels live on the manifest (index.tsx), and metadata-labelled
// columns/fields need none.
export const enTagsMessages: Record<string, string> = {
  "col.name": "Name",
  "col.color": "Color",
  "col.attention": "Attention",
  "col.category": "Category",
  "col.maxPerRecord": "Most per record",
  "form.details": "Details",
  "record.label": "Tags",
  "record.other": "Other tags",
  "record.error": "Could not update tags.",
};

export const useTagsT = createNamespaceT("tags", enTagsMessages);
