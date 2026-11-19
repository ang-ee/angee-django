import type { RuntimeBrand } from "@angee/ui/runtime";
import type { ShellSettings } from "./define-addon";

export function assertBrand(where: string, brand: unknown): asserts brand is RuntimeBrand;
/** Refuse a malformed `ANGEE_UI.shell`, which would otherwise select the wrong app silently.
 * @kind null
 * @reason Runtime infrastructure: composition, state, validation or lifecycle support without rendered anatomy.
 */
export function assertShellSettings(shell: unknown): asserts shell is ShellSettings;

/** Static head facts from validated shell settings; asset names remain unresolved. */
export function shellSiteSettings(shell: ShellSettings):
  (Omit<NonNullable<ShellSettings["site"]>, "title"> & { title: string; siteName?: string }) | undefined;
