---
"@angee/ui": patch
---

List search offers every search field the resource declares (the page's text field
first), not only the first one; a facet declared on an enum field matches its options
locally instead of being looked up as a relation.
