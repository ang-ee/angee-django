/** A source's commits ahead of and behind its upstream, in the one console idiom. */
export function formatDrift(ahead: number | null | undefined, behind: number | null | undefined): string {
  return `↑${ahead ?? 0} ↓${behind ?? 0}`;
}
