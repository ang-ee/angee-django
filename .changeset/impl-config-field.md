---
"@angee/ui": minor
---

`useImplConfigFields` takes the name of the model's JSON field that holds the
chosen implementation's configuration (a fourth argument, `config` when left
out), so a model that keeps it under another name, such as `target_config`,
edits it field by field from the implementation's own schema as well.
