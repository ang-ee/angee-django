/** Return the custom two-record comparison route for a pair of public party ids. */
export function partyMergePath(left: string, right: string): string {
  return `/parties/merge/${encodeURIComponent(left)}/${encodeURIComponent(right)}`;
}
