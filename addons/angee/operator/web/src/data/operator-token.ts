// The live daemon bearer for the `operator` refine data provider.
//
// The provider is built once at app composition, but the bearer is minted
// per-actor by the console and rotated on a timer (see the token gate in
// `transport.tsx`). `bearerAuthFromGetter` reads this store per request, so a
// rotation only calls `set` — the provider is never rebuilt. A module-scoped
// store fits: the console mounts one daemon connection at a time, so there is a
// single current token; the gate owns writes and the provider owns reads.

let token: string | null = null;

/** The current daemon bearer (read per request by the provider), or null. */
export const operatorToken = {
  /** Read the live bearer; null before the gate connects or after it clears. */
  get(): string | null {
    return token;
  },
  /** Set or rotate the live bearer; pass `null` to clear on disconnect. */
  set(next: string | null): void {
    token = next;
  },
};
