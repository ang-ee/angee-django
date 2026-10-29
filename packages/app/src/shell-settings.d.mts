import type { RuntimeBrand } from "@angee/ui/runtime";
import type { ShellSettings } from "./define-addon";

export function assertBrand(where: string, brand: unknown): asserts brand is RuntimeBrand;
/** Refuse a malformed `ANGEE_UI.shell`, which would otherwise select the wrong app silently.
 * @kind null
 * @reason Runtime infrastructure: composition, state, validation or lifecycle support without rendered anatomy.
 */
export function assertShellSettings(shell: unknown): asserts shell is ShellSettings;
