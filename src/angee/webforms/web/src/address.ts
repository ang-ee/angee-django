import { angee } from "../package.json";

// The path the package's standalone page declares, `/forms/w/*`: the one place the
// web side names where a form is reached. An address is that prefix and a slug.
const prefix = angee.pages["webforms.form"].paths[0]!.slice(0, -1);

/** The public address a form answers at for one of its slugs. */
export function publicAddress(slug: string): string {
  return `${prefix}${slug}`;
}
