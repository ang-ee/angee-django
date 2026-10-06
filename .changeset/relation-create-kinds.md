---
"@angee/metadata": minor
"@angee/ui": minor
---

Resource metadata lists an MTI parent's `concreteKinds`: the direct child models the
schema exposes, in model-label order. A relation picker on such a parent creates
through one dialog with a kind switcher over the parent itself (when creatable) and
each creatable kind; the chosen kind's form saves through its own create root and
the new record is selected under the parent. `RelationCreateConfig.kinds` carries
the choices, and `RelationCreateKind` describes one. `RelationFieldWidget` accepts
`aria-labelledby`.
