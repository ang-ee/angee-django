/**
 * Mint an opaque client request identity in secure and non-secure browser contexts.
 * Callers retain the returned value across retries; the server remains the durable
 * idempotency owner.
 * @kind null
 * @reason Codec and transport infrastructure: translates runtime data and addresses.
 */
export function createClientKey(prefix = "request"): string {
  const crypto = globalThis.crypto;
  if (typeof crypto?.randomUUID === "function") return crypto.randomUUID();
  if (typeof crypto?.getRandomValues === "function") {
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    return `${prefix}-${Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("")}`;
  }
  return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}
