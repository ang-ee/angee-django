import type { BaseAddon } from "@angee/base";

import { NotePage, NoteRecordPage } from "./NotePage";

/** The notes addon: one console route and a menu entry pointing at it. */
const notes: BaseAddon = {
  id: "notes",
  routes: [
    {
      name: "notes.home",
      path: "/notes",
      shell: "console",
      title: "Notes",
      icon: "notes",
      breadcrumbs: [{ label: "Notes" }],
      component: NotePage,
    },
    {
      name: "notes.record",
      path: "/notes/$id",
      shell: "console",
      title: "Notes",
      icon: "notes",
      breadcrumbs: [{ label: "Notes", to: "/notes" }],
      component: NoteRecordPage,
    },
  ],
  menus: [{ id: "notes", label: "Notes", to: "/notes", icon: "notes" }],
};

export default notes;
