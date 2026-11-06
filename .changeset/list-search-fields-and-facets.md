---
"@angee/ui": patch
"@angee/refine": patch
---

List search offers every search field the resource declares (the page's text field
first), not only the first one; server facet reads activate while the search box is in
use — typing as well as the panel — so facet suggestions appear while typing; and a
facet option takes the bucket's own label (the server labels relation buckets beside the
key), so relation facets show names instead of public ids; a facet declared on an enum
field matches its options locally instead of being looked up as a relation.
