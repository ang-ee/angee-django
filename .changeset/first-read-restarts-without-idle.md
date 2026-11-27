---
"@angee/refine": patch
---

A write's refresh no longer leaves an unanswered first read idle on its way to
being replaced. `invalidateAuthoredQueries` cancels a read of a moved model that
has not answered yet, so that a request sent before the write cannot commit
after it, and, if the read is active, sends it again. It awaited the
cancellation before the resend, and between the two the read was neither
answered nor on its way: `isFetching` and `isLoading` were false with no data. A
reader that waits for a first answer takes that for an answer that will not
come.

A record form's confirmation used to come after this refresh; since it confirms
on the write, it renders in that gap.

The cancellation and the resend now happen in one synchronous call. The
cancellation rejects the request synchronously, so the resend is a new request,
and reverts the read synchronously, so nothing overwrites the resend's state;
the resend's answer is the one committed. `useSetAuthoredQueryData`, which
cancelled and awaited the same way before accepting a result, is changed the
same way; and when its updater accepts nothing it no longer cancels the read,
which it used to leave idle with no data and nothing to resend it.

What else changes: two writes whose refreshes start within a few microtasks of
each other during one unanswered first read each restart it (three requests,
where the awaited cancellation sent two), as writes further apart already did,
and the first caller's promise resolves when its own resend is replaced.
