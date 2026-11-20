import { createNamespaceT } from "@angee/ui/i18n";

// Only the keys the pages and the form resolve. The menu labels live on the
// manifest (index.tsx), and metadata-labelled columns and fields need none.
export const enWebformsMessages: Record<string, string> = {
  "form.target": "Target",
  "form.targetConfig": "Target configuration",
  "form.theme": "Theme",
  "form.themeHint": "The theme the public page wears. Leave it empty to follow the default theme.",
  "form.retention": "Retention in days",
  "form.retentionHint": "How long a delivered response is kept. Leave it empty to keep responses.",
  "form.open": "Open",
  "form.close": "Close",
  "form.closeConfirm": "The form stops taking submissions. The responses it has received stay.",
  "version.personal": "Personal questions",
  "version.personalHint": "The names of the questions that hold personal data. An empty list states that none do.",
  "version.contact": "Contact question",
  "version.publish": "Publish",
  "version.publishConfirm": "The form serves this version from now on. A published version never changes again.",
  "response.empty": "No responses yet.",
  "response.deliver": "Deliver",
  "response.dismiss": "Dismiss",
  "response.dismissConfirm": "The response is kept and is no longer handed to the form's target.",
  "response.erase": "Erase personal answers",
  "response.eraseConfirm": "The answers this response's version marks as personal are blanked. This cannot be undone.",
};

export const useWebformsT = createNamespaceT("webforms", enWebformsMessages);
