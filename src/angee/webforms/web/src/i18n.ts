import { createNamespaceT } from "@angee/ui/i18n";

// Only the keys the pages and the form resolve. The menu labels live on the
// manifest (index.tsx), and metadata-labelled columns and fields need none.
export const enWebformsMessages: Record<string, string> = {
  "form.target": "Target",
  "form.targetConfig": "Target configuration",
  "form.theme": "Theme",
  "form.themeHint": "The theme the public page wears. Leave it empty to follow the default theme.",
  "form.retention": "Retention in days",
  "form.retentionHint": "How many days a response is kept after it was received, from 1 to 36,500. Leave it empty to keep responses.",
  "form.tabs.addresses": "Addresses",
  "form.undelivered": "Undelivered",
  "form.undeliveredCount": "{count} undelivered",
  "form.undeliveredExpiring": "{count} undelivered, the first expires on {date}",
  "form.undeliveredHint": "A response is removed once the form's retention has passed, delivered or not.",
  "form.undeliveredShow": "Show undelivered responses",
  "form.open": "Open",
  "form.close": "Close",
  "form.closeConfirm": "The form stops taking submissions. The responses it has received stay.",
  "alias.address": "Address",
  "alias.empty": "No address yet. A form is reached by the public only at an address.",
  "alias.slugHint": "Lower-case letters and numbers, with single hyphens between.",
  "alias.languageHint": "The language this address opens the form in, such as en or pt-BR. Leave it empty to follow the visitor's.",
  "version.personal": "Personal questions",
  "version.personalHint": "The names of the questions that hold personal data. An empty list states that none do.",
  "version.contact": "Contact question",
  "version.publish": "Publish",
  "version.publishConfirm": "The form serves this version from now on. A published version never changes again.",
  "response.empty": "No responses yet.",
  "response.undelivered": "Undelivered",
  "response.expires": "Expires",
  "response.deliver": "Deliver",
  "response.dismiss": "Dismiss",
  "response.dismissConfirm": "The response is kept and is no longer handed to the form's target.",
  "response.erase": "Erase personal answers",
  "response.eraseConfirm": "The answers this response's version marks as personal are blanked. This cannot be undone.",
};

export const useWebformsT = createNamespaceT("webforms", enWebformsMessages);
