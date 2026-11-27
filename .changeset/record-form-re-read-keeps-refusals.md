---
"@angee/ui": patch
---

A record form that holds a draft keeps what a refused save said about it across
a re-read of its record: the server's refusal stays under its field until the
person changes that field or discards. A re-read that replaces a value the
person has not changed takes that value's error away, a member path's
(`config.root`) included, and a re-read that leaves no draft, because another
writer stored the person's own values, takes the draft's errors with it. A form
holding no draft is reset whole, as before. Every record form behaves so, not
only one repairing an implementation selector.
