/**
 * The admin-list read cap. The identity console's list pages (users, roles,
 * grants, relationships, connections) fetch the whole set in one page so the
 * framework list toolbar can group and filter it client-side; this bounds that
 * "fetch all" so a large tenant can't pull an unbounded result. Pages that show
 * a "showing first N" notice read this same value, so the cap and the message
 * stay in sync.
 */
export const IAM_LIST_LIMIT = 500;
