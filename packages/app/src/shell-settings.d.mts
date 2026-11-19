import type { RuntimeBrand } from "@angee/ui/runtime";
import type { ShellSettings } from "./define-addon";

export function assertBrand(where: string, brand: unknown): asserts brand is RuntimeBrand;
/** Refuse a malformed `ANGEE_UI.shell`, which would otherwise select the wrong app silently.
 * @kind null
 * @reason Runtime infrastructure: composition, state, validation or lifecycle support without rendered anatomy.
 */
export function assertShellSettings(shell: unknown): asserts shell is ShellSettings;

/**
 * The static head the web build writes. `title` is `shell.site.title`, else
 * `shell.brand.name`; `site` is present only when `shell.site` is declared and
 * carries the description, link-preview and icon facts (`siteName` is the shell
 * brand's name). Asset slots hold `angee.assets` names until `generateWebRuntime`
 * resolves them to absolute canonical files.
 */
export interface SiteHead {
  title: string;
  site?: Omit<NonNullable<ShellSettings["site"]>, "title"> & { siteName?: string };
}

/** The static head validated shell settings ask for: a brand alone gives only the title. */
export function shellSiteHead(shell: ShellSettings): SiteHead | undefined;
