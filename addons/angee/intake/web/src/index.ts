import { defineBaseAddon } from "@angee/app";

// Slice 5.1-5.3 contributes the package contract only. Task/project panes and
// channel configuration UI belong to Slice 5.4.
const intake = defineBaseAddon({ id: "intake" });

export default intake;
