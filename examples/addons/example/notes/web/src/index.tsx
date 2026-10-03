import { defineBaseAddon } from "@angee/app";
import { lazyRouteComponent } from "@tanstack/react-router";

import { DemoForgotPasswordHint } from "./demo-auth";
import { enNotesMessages } from "./i18n";
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
  i18n: { notes: enNotesMessages },
  // Notes contributes only its star; IAM contributes Share globally.
  containers: {
    "form#chrome": {
      "notes.record-chrome": { sequence: 10, content: <RecordChrome /> },
    },
    // Example-only login help: the seeded demo credentials surface on the host's
    // login page through its container, so no host main.tsx wiring is needed.
    "auth.login#password-help": {
      "notes.demo-logins": { content: <DemoForgotPasswordHint /> },
    },
  },
});

export default notes;
