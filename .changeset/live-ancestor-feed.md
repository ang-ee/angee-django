---
"@angee/refine": patch
---

Angee's live provider delivers a change on a change feed that several resources
share under each of their models. A multi-table child model without a feed of
its own reads its nearest ancestor's feed, so a change published there now
refreshes the child's readers as well.
