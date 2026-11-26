---
"@angee/app": minor
---

The app's rule for retrying a failed read has a name: `retryAppQuery`, exported
from `@angee/app/create-app` (three retries, for a failure a retry can fix; a
public GraphQL answer such as a refusal is not retried). The app's query client
reads it as before. A surface that builds its own query client passes it so its
reads fail as the app's do.

The workshop's shared runtime fixture now does: a story about a refused read
showed its error late (the data library's default retried the refusal three
times, with a growing pause between tries). Only the retry rule is shared: the
fixture's other query defaults (how long a read stays fresh, how long it is
kept) are still the data library's, not the app's. The delay and its cure were
read from the code and pinned by a test of the fixture's client; neither was
timed in a browser.
