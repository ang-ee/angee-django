---
"@angee/refine": minor
"@angee/app": patch
---

Remove the generic authored infinite hook and its retained-row archive. Domain infinite queries use native TanStack Query with the existing exported transport, model-interest and error-policy helpers. Messaging and Nexus now retain history in native InfiniteData and revalidate loaded messages through scoped server windows and bounded ID batches.

Refresh interested authored reads when WebSockets reconnect, including changes received while the first read is pending. Reset observed queries and remove unobserved session data after successful login/logout so old responses and identity cannot survive an auth transition.
