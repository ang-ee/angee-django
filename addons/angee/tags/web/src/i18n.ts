import { createNamespaceT } from "@angee/ui";

// Only the keys the pane/page resolve — the sidebar/chatter chrome labels live on
// the manifest (index.tsx), and metadata-labelled columns/fields need none.
export const enTagsMessages: Record<string, string> = {
  "pane.assigned": "Tags",
  "pane.empty.record": "Open a record to manage its tags.",
  "pane.empty.none": "No tags yet.",
  "pane.add": "Add",
  "pane.add.empty": "Every tag is applied.",
  "pane.error": "Could not load tags.",
  "col.name": "Name",
  "col.color": "Color",
  "form.details": "Details",
};

export const useTagsT = createNamespaceT("tags", enTagsMessages);
