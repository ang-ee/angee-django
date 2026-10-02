import { defineAddon } from "@angee/app";

/** The suite's shell defaults; a product layered on the suite overrides them. */
export default defineAddon({
  id: "pm",
  shell: { home: "projects.my-work" },
});
