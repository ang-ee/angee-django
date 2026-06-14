import { defineBaseAddon, FORM_VIEW_RECORD_CHROME_SLOT } from "@angee/base";

import { NoteCrumb, NotePage } from "./NotePage";
import { RecordChrome } from "./RecordChrome";

/** The notes addon: one console surface and a menu entry pointing at it. The
 * record route nests under the list route — `NotePage` reads its `$id` param
 * and the route's crumb resolves the note title. */
const notes = defineBaseAddon({
  id: "notes",
  routes: [
    {
      name: "notes.home",
      path: "/notes",
      shell: "console",
      component: NotePage,
    },
    {
      name: "notes.record",
      path: "/notes/$id",
      shell: "console",
      parent: "notes.home",
      crumb: (match) => (
        <NoteCrumb id={String((match.params as { id?: string }).id ?? "")} />
      ),
    },
  ],
  menus: [{ id: "notes", label: "Notes", route: "notes.home", icon: "notes" }],
  // The record-form star/share chrome is host-provided, not baked into base.
  slots: [
    {
      slot: FORM_VIEW_RECORD_CHROME_SLOT,
      id: "notes.record-chrome",
      content: <RecordChrome />,
    },
  ],
});

export default notes;
