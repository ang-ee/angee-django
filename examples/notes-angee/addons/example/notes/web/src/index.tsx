import type { BaseAddon } from "@angee/base";

import { NoteCrumb, NotePage } from "./NotePage";

/** The notes addon: one console surface and a menu entry pointing at it. The
 * record route nests under the list route — `NotePage` reads its `$id` param
 * and the route's crumb resolves the note title. */
const notes: BaseAddon = {
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
};

export default notes;
