import { createContext } from "react";

/** Internal list decision shared by collection reads, including its dashboard.
 * ListView derives it from its presentation and enclosing saved record.
 */
export const ResourceViewArchiveScopeContext = createContext(true);
