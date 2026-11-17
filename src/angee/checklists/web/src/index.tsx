import { defineBaseAddon } from "@angee/app";

import { enChecklistsMessages } from "./i18n";
import { checklistTones } from "./ChecklistViews";

export {
  CHECKLIST_ASSESSMENTS, CHECKLIST_ATTENTION, CHECKLIST_FIELDS, CHECKLIST_STATUSES,
  ChecklistLegend, ChecklistSection, ChecklistStatusBadge,
  checklistCategoryColumns, checklistFromRow, checklistItemsColumn, checklistStatusColumn, checklistToneKey,
  checklistTones, useChecklistStatusLabel,
  type ChecklistAssessment, type ChecklistCategoryColumn, type ChecklistColumnOptions, type ChecklistSectionProps,
  type ChecklistStatus,
} from "./ChecklistViews";
export { ChecklistFragment, type ChecklistFields } from "./documents.console";
export { useChecklistsT } from "./i18n";

/**
 * The `@angee/checklists` rendered addon: the checklist status vocabulary and
 * tones, and the presentation an owner places for a node composing the
 * backend's `ChecklistNode`: the record section, the list columns and the
 * legend. Placement (which record tab, which list) stays with the owner.
 */
const checklists = defineBaseAddon({
  id: "checklists",
  i18n: { checklists: enChecklistsMessages },
  statusTones: checklistTones,
});

export default checklists;
