---
"@angee/refine": minor
---

`useAuthoredKeysetFeed` takes an optional `subscription` (`{ document,
variables }`): a dedicated GraphQL subscription that refreshes only that feed, in
place of subscribing to its models' change feeds. Its deliveries are coalesced
like other live reads, so a feed such as a record's timeline no longer refetches
on every change of the record's model.
