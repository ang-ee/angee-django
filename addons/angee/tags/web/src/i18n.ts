import { createNamespaceT } from "@angee/ui";

// Only the keys the field widget and page resolve — the shell chrome labels live
// on the manifest (index.tsx), and metadata-labelled columns/fields need none.
export const enTagsMessages: Record<string, string> = {
  "field.label": "Tags",
  "field.unsaved": "Save the record to add tags.",
  "col.name": "Name",
  "col.color": "Color",
  "form.details": "Details",
};

export const useTagsT = createNamespaceT("tags", enTagsMessages);
