import { defineBaseAddon } from "@angee/app";
import { FORM_VIEW_RECORD_CHROME_SLOT } from "@angee/ui";
import { lazyRouteComponent } from "@tanstack/react-router";

import { RecordChrome } from "./RecordChrome";

/** The notes addon: one console surface and a menu entry pointing at it. The
 * record route nests under the list route — `NotePage` reads its `$id` param. */
const notes = defineBaseAddon({
  id: "notes",
  routes: [
    {
      name: "notes.home",
      path: "/notes",
      layout: "console",
      resource: "notes.Note",
      component: lazyRouteComponent(() => import("./NotePage"), "NotePage"),
    },
    {
      name: "notes.record",
      path: "/notes/$id",
      layout: "console",
      parent: "notes.home",
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
