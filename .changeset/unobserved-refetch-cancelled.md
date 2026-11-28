---
"@angee/refine": patch
---

A caller's write cancels a refetch that no enabled observer watches.
`invalidateAuthoredQueries` cancelled only reads with no data, and refetched only
active reads. A read that had data and was refetching when its last observer
left (the person navigated away; the transport does not abort), or whose
observers are all disabled, was neither cancelled nor refetched: its request,
sent before the write, committed its answer afterwards and cleared the invalid
mark, so returning within the stale time showed the answer from before the
write, with no refetch.

That refetch is now cancelled with the others. The read keeps the data it had,
stays invalid, and refetches when an enabled observer mounts, as an inactive
read that was not fetching already did. For a read that fetches only on demand
(a disabled observer whose person asked for a refresh), the refresh asked for
before the write is dropped and the read shows what it held, marked invalid;
before, it committed the answer from before the write, which missed the write
as well.

A refetch an enabled observer watches is replaced as before, and whoever awaits
it still receives the resend's answer.
