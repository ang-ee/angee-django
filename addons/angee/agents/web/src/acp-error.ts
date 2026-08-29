// Turn a caught ACP/transport failure into a message fit for the user.

/**
 * The human message of a caught value, else `fallback`.
 *
 * A failed ACP request rejects with the raw JSON-RPC error object
 * (`{ code, message, data }`), *not* an `Error`: the connection does
 * `reject(response.error)` (see @agentclientprotocol/sdk
 * `#handleResponse`). So an agent-side failure — e.g. a 401 from its model API —
 * carries its detail on that object's `message`, which must be read off the
 * object shape too, not just `Error.message`, or it is dropped for the fallback
 * and the user only sees "did not respond" with the real cause hidden in logs.
 */
export function messageOf(caught: unknown, fallback: string): string {
  if (caught instanceof Error && caught.message !== "") return caught.message;
  if (
    typeof caught === "object" &&
    caught !== null &&
    "message" in caught &&
    typeof caught.message === "string" &&
    caught.message !== ""
  ) {
    return caught.message;
  }
  return fallback;
}
