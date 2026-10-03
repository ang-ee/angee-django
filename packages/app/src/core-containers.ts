import type { CoreContainer } from "@angee/ui/runtime";
import { FORM_CONTAINERS } from "@angee/ui/views/FormView";
import { RESOURCE_CONTAINERS } from "@angee/ui/views/index";
import { SHELL_CONTAINERS } from "@angee/ui/chrome/index";
import { CHATTER_CONTAINERS } from "@angee/ui/communication/index";

import { LOGIN_CONTAINERS } from "./auth/LoginPage";

/**
 * The containers the framework's render owners declare. Each owner adds its
 * container name to `ContainerKinds`, which types the `containers` entries.
 */
export const CORE_CONTAINERS: readonly CoreContainer[] = [
  ...SHELL_CONTAINERS,
  ...FORM_CONTAINERS,
  ...RESOURCE_CONTAINERS,
  ...CHATTER_CONTAINERS,
  ...LOGIN_CONTAINERS,
];
