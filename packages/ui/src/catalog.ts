import entries from "./catalog-data.json";
import { parseCatalog } from "./catalog-schema";

export * from "./catalog-schema";

/** Generated from declaration tags and CSF metadata; validated once at import. */
export const componentCatalog = parseCatalog(entries);
